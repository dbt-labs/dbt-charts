//! Bar family — horizontal bar charts and vertical column charts (single-series and grouped).
//!
//! Orientation "horizontal": category on the vertical (band) axis, value on the horizontal
//! (linear) axis.  Grouped when `resolved.color` is set and `stack = "none"`.
//!
//! Orientation "vertical": category on the horizontal (band) axis, value on the vertical
//! (linear) axis — VL column chart.
//!
//! `link` (chart-root `ResolvedChart.link`, a channel-placeholder template) is wired on
//! the single-series (no `color`), unstacked, unlayered, no-`support_table`,
//! band-scale or quantitative-x bar, vertical and horizontal: one `<a href>` per datum,
//! inside its `.role-mark` group, wrapping the datum's first drawn element, the
//! value percent-encoded by `_vega_encode_field`'s chain. Refuses: a `color`,
//! stacked, `layers`, or `support_table` bar, and a
//! `{{ color }}`/`{{ theta }}` placeholder. A date x reads `{{ x }}` as `0NaN-NaN-NaN`
//! (`timeFormat` of a band-scale string).
//!
//! Domain-sort conventions:
//!   - Authored `chart.sort`: the sort field summed per category, either
//!     orientation (production's `stacked_x_domain_order`).
//!   - Horizontal single-series: value-descending (production pins a field sort).
//!   - Everything else: query order (production pins `sort: null` — an explicit
//!     literal on both orientations, never the absent key VL would sort).
//!   The table with its production sources is on `CatOrder`.
//!
//! Stacking (`stack = "zero"` or `"normalize"`, always paired with a `color`
//! series channel): segments accumulate per category in *stack order* —
//! default (and only supported) order is descending global series sum, index 0
//! at the baseline (x=0 for horizontal, the value-axis zero for vertical) and
//! subsequent series stacked outward. Matches production's
//! `sorted_series_by_stack_order(..., stack_order=None)` +
//! `series_order_expression` / VL `order` channel (ascending sort on that
//! computed index) — see `emitters/bar.py`. Segment fill color follows that
//! same baseline-first order, so the baseline series receives palette slot 0.
//! `stack = "zero"` uses the baked
//! `stacked_domain_max` for the shared value-axis domain; `stack = "normalize"`
//! divides each segment by its category total and fixes the domain to
//! `[0, 1]` with a hardcoded quartile tick ladder + percent labels (mirrors
//! Python's `_NORMALIZE_QUARTILE_TICKS`, since VL auto-formats a normalized
//! stack's axis as a percentage regardless of any authored format).
//!
//! A wide (`y: [a, b]`) chart unfolds into long rows once, at the top of
//! `render`, before any other law runs — see `common::unfold_wide_rows`'s
//! doc comment. Everything downstream (grouped sub-bands, top legend,
//! palette) runs unchanged, same as an authored `color:` chart.
//!
//! Unsupported shapes → clear `Err`:
//! - multiples layout
//! - layers beyond the modeled shapes (`validate_layers`; `docs/laws-layers.md`)
//! - a wide chart with no `x` field (`BarResolved.x` is a required
//!   `String`, so this refuses via a plain deserialize error, wide or not)
//! - orientation other than "horizontal" / "vertical"
//! - log scale on the value axis
//! - resolved_channels.color.mode == "conditional" present (bar's authored
//!   surface can no longer produce this from a real board; guards a
//!   hand-written or stale resolved-JSON fixture instead)

pub(crate) mod axes;
pub mod bind;
mod continuous;
mod domain;
mod facet;
mod horizontal;
pub(crate) mod labels;
pub mod layout;
pub(crate) mod legend;
pub(crate) mod marks;
mod time_x;
mod tooltip;
mod vertical;

use self::facet::{color_one_to_one, render_facet};
use self::marks::overlap_fraction;

use crate::codes;
use crate::constants::bar::CONTINUOUS_BAR_MAX_SIZE;
use crate::diagnostic::Diagnostic;
use std::collections::HashMap;

use serde::Deserialize;
use serde_json::Value;

use crate::common::{
    row_num, row_str,
    AxisStyle, ChartSort, FormatSlot, MarkLabels, StyleChannel, TitleStyle,
};
use crate::families::empty_cartesian::ValueAxis;
use crate::families::support_table_strip;
use crate::families::color_scale::ContinuousColor;
use crate::families::legend::{
    legend_title_text, LegendConfig,
};
use crate::layout::Rect;
use measure::{FontBook, Measure};
use crate::Payload;

// Fixed chrome (title + value axis label row) in pixels — matches _LAYOUT_CHROME_PX in Python.
pub(crate) const CHROME_PX: f64 = 72.0;
// Default quartile tick grid for a `stack = "normalize"` value axis —
// matches `_NORMALIZE_QUARTILE_TICKS` in `emitters/bar.py`.
pub(crate) const NORMALIZE_QUARTILE_TICKS: [f64; 5] = [0.0, 0.25, 0.5, 0.75, 1.0];

// ── Payload structs ──────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub(crate) struct BarResolved {
    /// Set where a wide chart with no authored `color` folds: its series are the `y` measure names.
    #[serde(skip)]
    series_from_measures: bool,
    /// The rows as queried, set where a wide chart folds: a support_table strip reads them.
    #[serde(skip)]
    prefold_rows: Vec<serde_json::Map<String, Value>>,
    id: String,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    title: Option<String>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    subtitle: Option<String>,
    title_style: Option<TitleStyle>,
    /// Effective chart background: chart-local `style.background` when
    /// authored, else the board background — production's own
    /// `_SharedResolvedChartFields.background`
    /// (`compile/models/chart/resolved/_base.py:158`). This is the fill
    /// production sets as the VL spec background behind the canvas rect
    /// (`render/chart/session.py:184`, `presentation.py:74`), NOT
    /// `Payload.background` (the board-level default only) — the two diverge
    /// whenever a chart authors its own `style.background`. It is the canvas
    /// rect's fill alone: ink reads `canvas`.
    background: String,
    /// The chart's opaque composited canvas
    /// (`_SharedResolvedChartFields.canvas`,
    /// `compile/models/chart/resolved/_base.py:169`): the ink an inside-bar
    /// value label takes (`features/value_labels.py:1572,1585`) and the
    /// value labels of every bar layer (`emitters/_overlay.py:516,1992`),
    /// where `background` may be `transparent`. Never the canvas rect's fill.
    #[serde(deserialize_with = "crate::common::de_canvas")]
    canvas: String,
    /// Category field (x in the model; band axis).
    x: String,
    /// The date range a query filter proves for x, which production pins a time axis to.
    #[serde(default)]
    x_date_range: Option<time_x::DateRange>,
    /// Value field (y in the model; linear axis).
    y: String,
    #[serde(default)]
    annotations: Vec<crate::annotation::Annotation>,
    #[serde(default)]
    x_label: Option<String>,
    #[serde(default)]
    y_label: Option<String>,
    field_labels: crate::common::FieldLabels,
    #[serde(default)]
    tooltip: Vec<crate::families::tooltip_extra::TooltipField>,
    color: Option<String>,
    /// Only the presence of `color` is read (a genuine-zero label needs a
    /// colorless bar).
    #[serde(default)]
    resolved_channels: HashMap<String, StyleChannel>,
    /// How the query prints the `color` column.
    #[serde(default)]
    color_format: Option<crate::common::FormatSlot>,
    /// The continuous scale a numeric `color` column draws, set at render.
    #[serde(skip)]
    numeric_color: Option<ContinuousColor>,
    /// Every queried row when rows with a null numeric color were dropped: a dropped row
    /// draws no bar but its category keeps its place on the axis.
    #[serde(skip)]
    all_rows: Option<Vec<serde_json::Map<String, Value>>>,
    /// Authored `chart.sort` — production honors it on both orientations
    /// (`CatOrder::Field`).
    #[serde(default)]
    sort: Option<ChartSort>,
    orientation: String,
    stack: String,
    palette: Vec<String>,
    /// The palette a series color reads: the chart's own, else the board's, else Vega's default.
    #[serde(skip)]
    series_palette: Vec<String>,
    /// Board-wide value→slot bindings for this chart's categorical fields
    /// (#7167) — see `common::category_scale_for`/`series_color`.
    #[serde(default)]
    category_colors: Vec<crate::common::CategoryColorScale>,
    multiples: Option<crate::families::multiples::MultiplesConfig>,
    /// Baked small-multiples partition dimensions (`resolved.panel_axes`) —
    /// present exactly when `multiples` is (resolve bakes one `PartitionAxis`
    /// per `multiples.rows`/`multiples.columns` field actually authored).
    #[serde(default)]
    panel_axes: Vec<crate::families::multiples::PartitionAxis>,
    #[serde(default)]
    wide_measures: Vec<String>,
    /// Column each bar starts from (`resolved/bar.py`); absent, bars start at
    /// zero. Not modeled — `render` refuses it.
    #[serde(default)]
    y_start: Option<String>,
    /// `"temporal"` only for a bar between two date columns. Not modeled —
    /// `render` refuses it.
    #[serde(default)]
    measure_type: Option<String>,
    #[serde(default)]
    layers: Vec<Value>,
    /// The chart's own resolved query name — every layer defaults to it
    /// (`compile/resolve/_layers.py`) unless it authors its own `query:`.
    /// `None` on any payload predating this field (every existing fixture);
    /// only the layers gate reads it, and only when `layers` is non-empty.
    #[serde(default)]
    query_name: Option<String>,
    legend: LegendConfig,
    style: BarStyle,
    /// Baked stacked-total-per-category max (with headroom), used as the
    /// shared value-axis domain for `stack = "zero"`. `bar.py:557-572` leaves
    /// this `None` for `stack = "normalize"` and for a non-positive total —
    /// not "always present" — and production falls back to `y_ticks[-1]`
    /// (`emitters/bar.py:889-892`) in that case. This renderer has no such
    /// fallback: a `stack = "zero"` chart with no baked value hard-errors
    /// (acceptable for a spike; a production port would need the fallback).
    #[serde(default)]
    stacked_domain_max: Option<f64>,
    /// A non-null `support_table` grows the card past the plot's own
    /// `payload.height` for the attached strip's rows (vertical) or the
    /// column block's header and ink (horizontal) — see `AGENTS.md`'s
    /// geometry contract.
    #[serde(default)]
    support_table: Option<Value>,
    /// The support_table strip's resolved style — present exactly when
    /// `support_table` is (baked at `compile.resolve.chart._kwargs`).
    #[serde(default)]
    effective_support_table_style: Option<support_table_strip::SupportTableStyle>,
    /// Resolved chart's own baked axis-label gap, required for a `position: bottom` strip.
    #[serde(default)]
    support_table_axis_offset: Option<f64>,
    /// Whether a per_series cell paints its series' dark ink (`font.color:
    /// auto`); every other cell paints `effective_support_table_style.font.color`,
    /// already the baked neutral color or the authored one.
    #[serde(default)]
    support_table_series_ink: Option<bool>,
    // Chart-root `ResolvedChart.link` (`_base.py:85`) — `links.auto`'s own
    // filter/entity-drill template (`automatic_link.py::synthesize_automatic_link`,
    // `resolve_column_set_link(row_detail=False)`), keyed by CHANNEL name
    // placeholders (`{{ x }}`, `{{ color }}` — never a column name; that
    // form is the table row-detail path, ported separately). Only wired on
    // the single-series (no `color`), unstacked, unlayered, non-temporal
    // band-scale bar, vertical or horizontal (`render()`'s own gate, and
    // `render_facet`'s own) — every other shape refuses by name rather than
    // silently drawing a mark with no href.
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    link: Option<String>,
    link_open: crate::href::LinkOpen,
    /// The host's board URL scheme, from the payload; rewrites each datum href.
    #[serde(skip)]
    link_context: Option<crate::href::LinkContext>,
    /// The columns a link's `{{ y }}` and `{{ color }}` read: the authored
    /// ones, or on a wide chart the first measure and the authored dimension
    /// (else the fold's raw measure name). Set before the fold overwrites
    /// `color`.
    #[serde(skip)]
    link_y: String,
    #[serde(skip)]
    link_color: Option<String>,
    /// The marks a held link variable selects: a series, or a single-series bar's category.
    #[serde(skip)]
    selection: crate::selected::Selection,
}

impl BarResolved {
    /// Whether the category domain defaults to largest-measure-first: a
    /// horizontal bar with no color channel and no `y_start`
    /// (`emitters/bar.py:1669-1674`); every other unauthored order keeps the
    /// query's.
    fn sorts_by_value_by_default(&self) -> bool {
        self.orientation == "horizontal" && self.color.is_none() && self.y_start.is_none()
    }
}

/// Band-scale padding of a grouped (side-by-side) bar
/// (`style.charts.bar.grouped`).
#[derive(Deserialize)]
struct GroupedPadding {
    padding_inner: f64,
    padding_outer: f64,
}

#[derive(Deserialize)]
struct BarStyle {
    grouped: GroupedPadding,
    axis_x: AxisStyle,
    axis_y: AxisStyle,
    /// The resolved measure format the structured tooltip's value row paints
    /// (`chart.style.tooltip_format`, `emitters/_tooltip.py::_value_expr`
    /// through `format_quantitative_expr`). Already on the wire
    /// (`ResolvedStyle.tooltip_format`, `_cartesian.py`) on every real
    /// payload -- always a real d3 spec, never `""`.
    /// `#[serde(default)]` only so the many hand-built test fixtures
    /// predating this field keep deserializing; an empty spec is `d3rs`'s
    /// own "unimplemented" one, so `tooltip_labels` (bar.rs's paint loops)
    /// gates on it being non-empty rather than ever formatting with it.
    #[serde(default)]
    tooltip_format: FormatSlot,
    /// The share of the chart width the category labels may occupy before
    /// production's crowding remedies fire (`label_usable_ratio`).
    #[serde(default)]
    label_usable_ratio: Option<f64>,
    /// Whether a value label's `format` is an SI-shaped alias production
    /// resolved to the house register (`resolve_label_format`,
    /// `compile/format.py`) — gates the narrative-notation substitution
    /// (`k`/`mn`/`bn`/`trn`) in `mark_labels`. `false` on a fixture
    /// predating this field reproduces the pre-port raw-d3 behavior.
    #[serde(default)]
    label_is_house: bool,
    /// `label_is_house` for the stack-total label's own format.
    #[serde(default)]
    total_label_is_house: bool,
    /// The size a value label paints at (`_effective_label_font_size`): the
    /// row-fit test's line height.
    #[serde(default)]
    label_font_size: Option<f64>,
    /// Width-resolved OBJECT title font (`style.title_font`): production
    /// sizes a chart title by its own card's width tier
    /// (`chart_title_spec`, compile/resolve/style/typography.py) and bakes
    /// the answer here, so this beats the un-tiered `title_style.font`.
    /// The same slot line/pie/scatter/table already read.
    #[serde(default)]
    title_font: Option<crate::common::Font>,
    mark: MarkStyle,
    single_series_fill: String,
    series_label: SeriesLabel,
    endpoint_labels: EndpointLabels,
    /// Author-facing stack ordering: `value` (the default when `None`,
    /// largest global sum first), `data`, or `alphabetical`. Any other
    /// string is refused by `common::sorted_series_by_stack_order`.
    #[serde(default)]
    stack_order: Option<String>,
    #[serde(default)]
    overlap: Option<Overlap>,
}

#[derive(Deserialize)]
struct SeriesLabel {
    font_family: String,
    font_size: f64,
    font_weight: String,
    /// Upright/slanted style production always stamps alongside the other
    /// three font props (`_series_label_font_props`) — required, not
    /// defaulted, since the resolved style always carries it.
    font_style: String,
    dark_companion_palette: Vec<String>,
    /// `(pinned color, ink)` pairs for the `category_colors` pins outside the
    /// palette (`common::category_ink`).
    pin_companions: Vec<(String, String)>,
    /// Minimum pixel gap between cascaded rail labels
    /// (`series_label.gap_px`, baked by the theme). Required at use on the
    /// rail arm; older fixtures without a rail never read it.
    #[serde(default)]
    gap_px: Option<f64>,
}

#[derive(Deserialize)]
struct EndpointLabels {
    visible: bool,
    height: f64,
    label_offset: f64,
}

/// An authored `style.overlap`: a keyword from production's table or a
/// bare fraction of the sub-band.
#[derive(Deserialize)]
#[serde(untagged)]
enum Overlap {
    Fraction(f64),
    Keyword(String),
}

/// Vega-Lite's floor under a grouped bar's `width` expression.
const MIN_COLUMN_WIDTH: f64 = 0.25;

/// Vega-Lite's default view step (20 px) less two: the width of a bar on a point scale.
const VIEW_STEP_BAR_SIZE: f64 = 18.0;

/// Mirrors Python's `continuous_bar_max_size`: the authored ceiling, else the
/// engine's.
fn continuous_max_size(mark: &MarkStyle) -> f64 {
    mark.max_size.unwrap_or(CONTINUOUS_BAR_MAX_SIZE)
}

/// A continuous-x bar's width law: the authored `size`, else
/// `continuous_bar_size_prop`'s `(step - gap).clamp(min_size, max_size)` over the
/// closest pair of distinct x positions. Production asserts `gap`/`min_size` only
/// when `size` is unset, so a payload missing one is a contract violation, never a default.
pub(crate) struct ContinuousWidth {
    size: Option<f64>,
    ladder: Option<(f64, f64, f64)>,
}

impl ContinuousWidth {
    pub(crate) fn of(mark: &MarkStyle) -> Result<Self, String> {
        let ladder = match mark.size {
            Some(_) => None,
            None => Some((
                mark.gap.ok_or("bar: style.mark.gap absent (theme required when mark.size is unset)")?,
                mark.min_size.ok_or("bar: style.mark.min_size absent (theme required when mark.size is unset)")?,
                continuous_max_size(mark),
            )),
        };
        Ok(Self { size: mark.size, ladder })
    }

    /// The width of a bar on a point scale: the authored `size`, else Vega-Lite's view step less two.
    pub(crate) fn stepped(mark: &MarkStyle) -> Self {
        Self { size: Some(mark.size.unwrap_or(VIEW_STEP_BAR_SIZE)), ladder: None }
    }

    /// The width with no pair of x positions to measure a step from: `continuous_bar_size_prop`'s literal
    /// `max_size`.
    pub(crate) fn lone(&self) -> f64 {
        match (self.size, self.ladder) {
            (Some(size), _) => size,
            (None, Some((_, _, max))) => max,
            (None, None) => unreachable!("a ladder resolved when the size is unset"),
        }
    }

    /// Vega's `Math.max(min, Math.min(max, x))`: an authored `min_size` above the ceiling wins.
    pub(crate) fn at(&self, step_px: f64) -> f64 {
        match (self.size, self.ladder) {
            (Some(size), _) => size,
            (None, Some((gap, min, max))) => (step_px - gap).min(max).max(min),
            (None, None) => unreachable!("a ladder resolved when the size is unset"),
        }
    }
}

/// A time bucket's bar as `(left, width)`: its inner `band_width` share, each edge
/// offset half a pixel by Vega-Lite (inward on a bucket narrower than a quarter
/// pixel). `start` and `end` are the bucket's pixel edges.
pub(crate) fn bucket_bar_extent(start: f64, end: f64, band_width: f64) -> (f64, f64) {
    let span = (end - start).abs();
    let edge = (1.0 - band_width) / 2.0;
    let narrow = (0.25 - span).max(0.0) * 0.5;
    let (from, to) = if span < 0.25 {
        (start + (1.0 - edge) * span + 0.5 + narrow, start + edge * span + 0.5 - narrow)
    } else {
        (start + (1.0 - edge) * span, start + edge * span + 1.0)
    };
    (from.min(to), (to - from).abs())
}

/// The share of a bucket a bar spans when no `band_width` is authored.
pub(crate) const DEFAULT_BAND_WIDTH: f64 = 0.8;

fn continuous_pad_px(mark: &MarkStyle, ax_x: &AxisStyle) -> Result<f64, String> {
    let widest = mark.size.unwrap_or_else(|| continuous_max_size(mark));
    let padding = ax_x.scale.get("padding").and_then(Value::as_f64).ok_or("bar: axis_x.scale.padding absent (theme required for a date x axis)")?;
    Ok(padding.max(widest / 2.0))
}

#[derive(Deserialize)]
pub(crate) struct MarkStyle {
    pub(crate) band_width: f64,
    // Unauthored bars no longer get a literal `size` from the theme default
    // (`_base.yaml` now sizes them via the gap/min_size/max_size ladder
    // instead) — a resolved-style payload can genuinely omit this key.
    #[serde(default)]
    size: Option<f64>,
    // Mirrors Python's `effective_bar_size`: an unauthored bar reserves
    // against the ceiling the width clamp can never exceed.
    #[serde(default)]
    max_size: Option<f64>,
    // The continuous-x width ladder (`continuous_bar_size_prop`): width =
    // clamp(min_adjacent_step_px − gap, min_size, max_size) when `size` is
    // unauthored. Theme-populated whenever `size` is unset.
    #[serde(default)]
    gap: Option<f64>,
    #[serde(default)]
    min_size: Option<f64>,
    pub(crate) border: MarkBorder,
    /// `style.marks.bar.opacity`: the fill's opacity. Only overlay bar layers read it; a
    /// base bar paints at the default 1.
    #[serde(default)]
    pub(crate) opacity: Option<f64>,
    #[serde(default)]
    pub(crate) labels: Option<MarkLabels>,
    /// The stack-total label (`features/value_labels.py::
    /// _build_bar_total_label_layer`). `visible` is `null` until authored.
    #[serde(default)]
    total_label: Option<TotalLabel>,
}

/// `style.marks.bar.total_label`: `visible` stays `null` until authored, and
/// the font is always resolved (`InheritSlot` from `charts.font`).
#[derive(Deserialize)]
struct TotalLabel {
    #[serde(default)]
    visible: Option<bool>,
    #[serde(default)]
    format: Option<FormatSlot>,
    #[serde(default)]
    dx: Option<f64>,
    #[serde(default)]
    dy: Option<f64>,
    #[serde(default)]
    font: Option<crate::common::Font>,
}

impl TotalLabel {
    /// The `above` text mark `_build_bar_total_label_layer` builds: the
    /// standard bar-end position map, with this label's own offset and font.
    fn as_mark_labels(&self) -> Result<MarkLabels, String> {
        let font = self.font.clone().ok_or("bar: total_label.font absent (theme required)")?;
        Ok(MarkLabels {
            visible: true,
            last_only: false,
            field: None,
            format: self.format.clone(),
            dx: self.dx,
            dy: self.dy,
            font: Some(font),
            position: Some("above".into()),
            stacked: None,
        })
    }
}

#[derive(Deserialize)]
pub(crate) struct MarkBorder {
    #[serde(default)]
    pub(crate) width: Option<f64>,
    #[serde(default)]
    pub(crate) color: Option<String>,
    #[serde(default)]
    pub(crate) radius: Option<f64>,
}

// ── Overlay layers (chart.layers) ────────────────────────────────────────────
//
// Bar-type, line, area and scatter layers share the base's x scale over the
// ordered union of every layer's own x column — see docs/laws-layers.md. A
// layer shape the arms below do not model (a wide, support_table or railed
// base, a color on a layer over a color-split base, ...) refuses by name in
// `validate_layers`. The types and the shared-scale line-layer machinery
// (`OverlayLineLayer`, row resolution, the mixed legend, the paint routine)
// live in `families::layers` — line.rs's own admitted shape calls the same
// code, as does each layer's own axis (`LayerAxis`).
// `OverlayBarLayer` stays bar-only: no other family admits
// a bar-type layer.

/// The bar shapes production raises an ERR for before it draws anything,
/// decided ahead of `render` so the engine reports the same code and params.
pub fn production_errors(payload: &Payload) -> Result<(), Diagnostic> {
    // An unparseable payload is `render`'s own error to report.
    let Ok(r) = BarResolved::deserialize(&payload.resolved) else { return Ok(()) };
    overlap_errors(&r, payload)
}

fn overlap_errors(r: &BarResolved, payload: &Payload) -> Result<(), Diagnostic> {
    let chart_id = || Value::from(payload.resolved.get("id").and_then(Value::as_str).unwrap_or_default());
    let wide = !r.wide_measures.is_empty();
    let authored = match &r.style.overlap {
        Some(Overlap::Fraction(_)) => true,
        Some(Overlap::Keyword(k)) => k != "auto",
        None => false,
    };
    if !authored || r.stack != "none" {
        return Ok(());
    }
    // A wide chart's folded series always carry the offset channel; a color
    // field carries it unless it is 1:1 with the category, per panel.
    let series = if wide {
        r.wide_measures.join(", ")
    } else {
        match r.color.as_deref() {
            Some(color) if !color_one_to_one(&r, payload, color)? => color.to_string(),
            _ => return Ok(()),
        }
    };
    // `_apply_overlap`: a continuous category axis has no band to divide, so
    // any authored overlap raises before the full-overlap check.
    if r.orientation == "vertical" {
        let banded = matches!(
            x_resolution(&r, &payload.rows)?.resolution.vl_type,
            crate::families::temporal::VegaType::Nominal | crate::families::temporal::VegaType::Ordinal
        );
        if !banded {
            return Err(Diagnostic::new(&codes::ERR_BAR_OVERLAP_CONTINUOUS_AXIS, [("axis", Value::from("x")), ("chart_id", chart_id())]));
        }
    }
    // `auto` is excluded above, so the series-count default does not matter here.
    let fraction = overlap_fraction(r.style.overlap.as_ref(), 0.0)?;
    if fraction >= 1.0 {
        return Err(Diagnostic::new(
            &codes::ERR_BAR_OVERLAP_FULL_NON_UNIQUE_COLOR,
            [("chart_id", chart_id()), ("series", Value::from(series))],
        ));
    }
    // `_count_series`: the distinct color values, one for a chart with none.
    let n_series = if wide { crate::common::wide_series_names(&r.wide_measures, r.color.as_deref(), &payload.rows).len() } else { crate::common::color_domain_of(&payload.rows, &series).len().max(1) };
    if fraction > 0.0 && n_series != 2 {
        return Err(Diagnostic::new(&codes::ERR_BAR_OVERLAP_SERIES_COUNT, [("chart_id", chart_id()), ("n_series", Value::from(n_series))]));
    }
    Ok(())
}

impl BarResolved {
    pub(crate) fn from_payload(payload: &Payload) -> Result<Self, String> {
        let mut r: Self = Deserialize::deserialize(&payload.resolved).map_err(|e| format!("bar payload: {e}"))?;
        r.series_palette = payload.series_palette(&r.palette);
        r.link_context = payload.link_context.clone();
        r.link_y = r.y.clone();
        r.link_color = r.color.clone();
        Ok(r)
    }
}

impl BarResolved {
    /// A numeric `color` column is a continuous scale, not a set of series: each
    /// mark takes the scale's color for its own value, one gradient legend names
    /// the scale, and no endpoint label rail names a series. An authored
    /// `style.color.gradient` supplies the ramp; a bare numeric color takes `blues`.
    fn use_numeric_color(&mut self, rows: &[serde_json::Map<String, Value>]) -> Result<(), String> {
        let channel = self.resolved_channels.get("color");
        let gradient = channel.filter(|ch| ch.mode == "gradient").and_then(|ch| ch.scale.as_ref());
        let quantitative = gradient.is_some() || channel.is_some_and(|ch| ch.is_continuous());
        let Some(field) = self.color.clone().filter(|_| quantitative && self.wide_measures.is_empty()) else { return Ok(()) };
        let Some(blues) = crate::families::layers::numeric_layer_color(rows, &field) else { return Ok(()) };
        let color = match gradient {
            Some(scale) => {
                let extent = |rows: &[serde_json::Map<String, Value>]| {
                    let (lo, hi) = rows
                        .iter()
                        .filter_map(|row| row_num(row, &field))
                        .fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), v| (lo.min(v), hi.max(v)));
                    (lo <= hi).then_some((lo, hi))
                };
                let kept = extent(rows).expect("a numeric color has a drawn value");
                let all = self.all_rows.as_deref().map_or(Some(kept), extent);
                ContinuousColor::gradient(scale, all, kept, self.legend.visible)?
            }
            None => blues,
        };
        let overrides = rows
            .iter()
            .filter_map(|row| row_num(row, &field).map(|v| (row_str(row, &field), color.fill(v))))
            .collect();
        self.category_colors.retain(|scale| scale.field != field);
        self.category_colors.push(crate::common::CategoryColorScale { field, slots: Default::default(), overrides });
        self.style.endpoint_labels.visible = false;
        self.numeric_color = Some(color);
        Ok(())
    }

    /// The tooltip's series row for a numeric color: the value as the legend prints it,
    /// under the color column's query format. A null cell has no value to print.
    fn numeric_series_names(&self, domain: &[String]) -> Result<std::collections::HashMap<String, String>, String> {
        let (Some(_), Some(format)) = (self.numeric_color.as_ref(), self.color_format.as_ref()) else { return Ok(Default::default()) };
        domain
            .iter()
            .filter(|name| !name.is_empty())
            .map(|name| {
                let value = crate::common::js_string_to_number(name).ok_or_else(|| format!("bar: numeric color value {name:?} is not a number"))?;
                Ok((name.clone(), format.paint_tick(value)?))
            })
            .collect()
    }

    /// The numeric color's legend part: a gradient, titled by the field.
    fn numeric_base(&self) -> Result<Option<(&ContinuousColor, bool, String, Option<&crate::common::FormatSlot>)>, String> {
        let Some(color) = self.numeric_color.as_ref() else { return Ok(None) };
        let field = self.color.as_deref().expect("a numeric color has its field");
        Ok(Some((color, true, legend_title_text(field, &self.legend, &self.field_labels)?, self.color_format.as_ref())))
    }
}

pub fn render(payload: &Payload, fonts: &FontBook) -> Result<String, Diagnostic> {
    let bound = bind::bind(payload)?;
    // A facet composite has no single plot rect, so it is composed panel by panel here, at the
    // string boundary.
    if bound.r.multiples.is_some() {
        return render_facet(bound, fonts);
    }
    let cell = Rect { x: 0.0, y: 0.0, width: bound.payload.width, height: bound.payload.height };
    let laid_out = layout::layout(&bound, cell, &Measure { fonts })?;
    laid_out.diagnostics.into_iter().for_each(crate::diagnostic::warn);
    Ok(print::print(&laid_out.nodes))
}

/// What only a bar's zero-row drawing reads beyond `empty::Head`.
#[derive(Deserialize)]
struct EmptyBarExtra {
    #[serde(default)]
    orientation: Option<String>,
    #[serde(default)]
    y_start: Option<String>,
}

/// A bar over no rows: production compiles `mark: bar` over an empty dataset,
/// so Vega draws no marks and the axes are degenerate
/// (`empty_cartesian.rs`, `docs/laws-empty.md`).
///
/// - Orientation: `_bar_orientation` returns `None` without a data sample
///   (`compile/resolve/chart/_channels.py:151`), and the emitter reads only
///   `orientation == "horizontal"` (`emitters/bar.py:305`), so a null
///   orientation is the vertical column chart. A horizontal one puts the value
///   axis along the bottom and the category axis on the left (the right under
///   `axis_y.position: right`).
/// - A `y_start` bar encodes `y` and `y2`, which take no zero, and a vertical
///   stack has no value domain either: no rung. Otherwise the one rung of the
///   zero-extent scale sits at mid-axis.
/// - No zero rule: `BaselineFeature.apply` returns before `_insert_zero_rule`
///   when the chart has no rows (`baseline.py:354`).
pub fn render_empty(payload: &Payload, fonts: &FontBook) -> Result<scene::Node, Diagnostic> {
    let head = crate::families::empty::Head::parse("bar", &payload.resolved)?;
    let extra: EmptyBarExtra = Deserialize::deserialize(&payload.resolved)
        .map_err(|e| format!("bar payload: {e}"))?;
    let horizontal = extra.orientation.as_deref() == Some("horizontal");
    let zero_rung = extra.y_start.is_none() && (horizontal || !head.stacked());
    crate::families::empty_cartesian::render("bar", payload, fonts, ValueAxis::Linear { horizontal, zero_rung })
}

/// The closest pair of distinct x values in `rows`' `field` column, infinite
/// when fewer than two (the ladder reads the column alone, whatever each row's y).
pub(crate) fn min_adjacent_step(rows: &[serde_json::Map<String, Value>], field: &str) -> f64 {
    min_gap(rows.iter().filter_map(|row| row_num(row, field)).collect())
}

/// The closest pair of distinct values in `xs`, infinite when fewer than two.
pub(crate) fn min_gap(mut xs: Vec<f64>) -> f64 {
    xs.sort_by(f64::total_cmp);
    xs.dedup();
    xs.windows(2).map(|w| w[1] - w[0]).fold(f64::INFINITY, f64::min)
}


/// The one x-type law over the whole chart's rows and panel fields, as
/// production resolves it once ahead of any panel split.
fn x_resolution(
    r: &BarResolved,
    chart_rows: &[serde_json::Map<String, Value>],
) -> Result<crate::families::x_type::FilledX, String> {
    let normalized = crate::families::temporal::normalize_labeled_temporal(chart_rows, &r.x)?;
    let panel_fields: Vec<&str> = r.panel_axes.iter().map(|a| a.field.as_str()).collect();
    crate::families::x_type::resolve_filled_x(
        &normalized,
        &r.x,
        &crate::families::x_type::XAxis::of(&r.style.axis_x),
        "bar",
        false,
        &panel_fields,
    )
}

impl BarResolved {
    /// Whether the support table's cells carry the chart's link: a ranged bar's table reads the
    /// bars' data only while its value labels make the spec layered.
    fn strip_linked(&self) -> bool {
        self.link.is_some() && (self.y_start.is_none() || self.style.mark.labels.as_ref().is_some_and(|l| l.visible))
    }

    /// The per-datum link (`ClickInteractivityFeature`). `{{ x }}` reads the
    /// row's own raw cell, which the band domain folds (a null and an empty
    /// string share a `row_str` key where Vega's `'' + datum` gives `"null"` and
    /// `""`). A date x on a band scale rides as a string, which
    /// `timeFormat(datum[x], '%Y-%m-%d')` reads as an invalid date, so it prints
    /// `0NaN-NaN-NaN`; a time scale's x is a real date.
    fn datum_link<'a>(&'a self, cat_field: &'a str, x_text: crate::href::LinkText) -> Option<crate::href::DatumLink<'a>> {
        Some(crate::href::DatumLink {
            template: self.link.as_deref()?,
            open: self.link_open,
            x: crate::href::LinkField { name: cat_field, text: x_text },
            y: Some(crate::href::LinkField {
                name: &self.link_y,
                text: if self.measure_type.as_deref() == Some("temporal") && self.y_start.is_some() {
                    crate::href::LinkText::Days
                } else {
                    crate::href::LinkText::Raw
                },
            }),
            color: self.link_color.as_deref().map(crate::href::LinkField::raw),
            context: self.link_context.as_ref(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::continuous::{render_vertical_continuous_x, XLayers};
    use super::domain::{build_data_map, niced_rail_domain, row_keys, value_domain_and_ticks, CenterStack};
    use super::marks::{
        bar_passes, baseline_rule_layers, baseline_rules, collapsed_bars, layer_day_numbers, painted_series_bars,
        BandColumns, Rounded,
    };
    use super::vertical::{shape, Shape};
    use crate::common::VALUE_AXIS_BREATHING_ROOM;
    use crate::families::layers::{OverlayBarLayer, OverlayLayer, OverlayLineLayer};
    use crate::vega_fit;
    use scene::Attrs;

    /// The continuous bar ladder reads the closest distinct pair, whatever the row order.
    #[test]
    fn min_gap_is_the_closest_distinct_pair() {
        assert_eq!(super::min_gap(vec![10.0, 2.0, 7.0, 7.0, 3.0]), 1.0);
        assert!(super::min_gap(vec![5.0, 5.0]).is_infinite());
    }

    use super::*;

    /// Renders in the en-US time vocabulary the fixtures' dates are drawn in; the
    /// engine entry point scopes the board's own.
    fn render(payload: &Payload, fonts: &FontBook) -> Result<String, Diagnostic> {
        let _en = crate::time_locale::en_us_scope();
        super::render(payload, fonts)
    }

    /// `marks.bar.max_size` caps a band's bar thickness and centers it; an
    /// unauthored one leaves the band-fraction bar as it was.
    /// A step layer over a grouped base turns the offset scale into a point
    /// scale: zero-width columns `band / (series + 1)` apart.
    #[test]
    fn a_step_layers_point_scale_spaces_zero_width_columns_by_one_more_than_the_series() {
        let cols = BandColumns::point_slots(120.0, 2);
        assert_eq!((cols.width, cols.left(10.0, 0), cols.left(10.0, 1)), (0.0, 10.0, 50.0));
    }

    /// Vega paints grouped bars in data order, which shows where they overlap:
    /// the row order, not series by series.
    /// A repeated `(category, series)` stacks over the earlier row, except on a ranged bar:
    /// Vega-Lite stacks nothing that has a `y2`.
    #[test]
    fn a_ranged_bar_never_tiers_a_repeated_key() {
        let rows: Vec<serde_json::Map<String, Value>> = (0..2)
            .map(|i| serde_json::json!({"cat": "X", "v": i}).as_object().expect("object").clone())
            .collect();
        let keys = |tiered| row_keys(&rows, "cat", None, tiered);
        assert_ne!(keys(true)[0], keys(true)[1], "a stacked bar tiers the second row");
        assert_eq!(keys(false)[0], keys(false)[1], "a ranged bar keeps both rows on one key");
    }

    #[test]
    fn grouped_bars_paint_in_row_order() {
        let rows: Vec<serde_json::Map<String, Value>> = [("X", "web"), ("X", "store"), ("Y", "web"), ("Y", "store")]
            .iter()
            .map(|(cat, series)| {
                serde_json::json!({"cat": cat, "series": series, "v": 1.0}).as_object().expect("object").clone()
            })
            .collect();
        let color_domain = vec!["store".to_string(), "web".to_string()];
        let cat_domain = vec!["X".to_string(), "Y".to_string()];
        let data_map = build_data_map(&rows, "cat", "v", Some("series"), true).expect("data map");
        let (painted, layers) =
            painted_series_bars(&color_domain, &cat_domain, &data_map, None, &rows, ("cat", "series"), None, true);
        let order: Vec<(&str, &str)> = painted.iter().map(|(_, series, cat)| (cat.as_str(), series.as_str())).collect();
        assert_eq!(order, [("X", "web"), ("X", "store"), ("Y", "web"), ("Y", "store")]);
        assert_eq!(layers, 1);
    }

    /// A zero-width column (a step layer turns a grouped base's offset scale
    /// into a point scale) still strokes its edge, which a zero-width `<rect>`
    /// would not.
    #[test]
    fn a_zero_width_column_paints_as_a_path() {
        let svg = print::print(
            &bar_passes(false, 10.0, 5.0, 0.0, 20.0, "#111", None, "#fff", 1.0, 0.0, Rounded::Positive, true, None, None, None)
                .unwrap(),
        );
        assert!(svg.contains("<path d=\"M10.00,5.00h0v20.00h0Z\""), "{svg}");
        assert!(!svg.contains("<rect"), "{svg}");
    }

    #[test]
    fn an_authored_max_size_caps_and_centers_a_band_bar() {
        let plain = BandColumns::new(100.0, 0.8, None, None);
        assert_eq!((plain.width, plain.pad), (80.0, 10.0));
        let capped = BandColumns::new(100.0, 0.8, Some(20.0), None);
        assert_eq!((capped.width, capped.pad), (20.0, 40.0));
        let loose = BandColumns::new(100.0, 0.8, Some(400.0), None);
        assert_eq!((loose.width, loose.pad), (80.0, 10.0));
    }

    fn painted_rules(positions: &[f64]) -> String {
        print::print(&baseline_rule_layers(1, positions, |pos| Attrs::new().set("y1", pos.to_string())).unwrap())
    }

    #[test]
    fn every_baseline_rule_is_hidden_from_screen_readers() {
        let hidden = "<g class=\"mark-rule role-mark layer_1_marks\" aria-hidden=\"true\">\
             <line y1=\"0\"/></g><g class=\"mark-rule role-mark layer_2_marks\" aria-hidden=\"true\">\
             <line y1=\"1\"/></g>";
        assert_eq!(painted_rules(&[0.0, 1.0]), hidden);
        assert!(!painted_rules(&[0.0]).contains("aria-label"));
    }

    #[test]
    fn a_drawn_rail_takes_the_rail_concat_and_everything_else_the_fit() {
        assert_eq!(shape(true), Shape::RailConcat);
        assert_eq!(shape(false), Shape::Fit);
    }

    /// The side of the legend's square swatch.
    const LEGEND_SWATCH: f64 = 10.0;

    // Real font family present in InterVariable.ttf; "sans-serif" is skipped by face_for().
    const TEST_FONT: &str = "'Inter Variable', Inter, sans-serif";

    /// Load the shipped fonts from the repo fonts dir.
    ///
    /// Tests that measure text must call this — `FontBook::empty()` fails on
    /// any `measure()` because no font faces are loaded.
    fn test_fonts() -> measure::FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir =
            format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        measure::FontBook::load_dir(&dir).expect("test fonts load")
    }

    /// The text content of the first `<text>` element in `snippet` --
    /// tolerant of the `.role-legend-label` `<g>` wrapper now sitting
    /// between a legend swatch and its label (`legend.rs::emit_vega_legend`/
    /// `emit_vertical_legend`), unlike a fixed `split('>').nth(N)` offset.
    fn legend_label_text(snippet: &str) -> &str {
        let text_tag = snippet.find("<text").expect("a <text> element follows the swatch");
        let open_end = snippet[text_tag..].find('>').expect("<text> tag closes") + text_tag + 1;
        &snippet[open_end..][..snippet[open_end..].find('<').expect("</text> closes the label")]
    }

    /// Every `<text x="...">` anchor whose content is `label` (as `>label<`),
    /// in document order.
    fn text_xs(svg: &str, label: &str) -> Vec<f64> {
        svg.match_indices(label)
            .map(|(pos, _)| {
                let tag = &svg[svg[..pos].rfind("<text x=\"").expect("a <text x=...> tag")..pos];
                tag["<text x=\"".len()..]
                    .split('"')
                    .next()
                    .unwrap()
                    .parse()
                    .expect("numeric x")
            })
            .collect()
    }

    fn make_vertical_payload(cats: &[(&str, f64)], grouped: bool) -> serde_json::Value {
        use serde_json::json;

        let rows: Vec<_> = if grouped {
            cats.iter()
                .flat_map(|(cat, val)| {
                    vec![
                        json!({"product": cat, "region": "North", "revenue": val}),
                        json!({"product": cat, "region": "South", "revenue": val * 0.6}),
                    ]
                })
                .collect()
        } else {
            cats.iter()
                .map(|(cat, val)| json!({"product": cat, "revenue": val}))
                .collect()
        };

        let color_field = if grouped { Some("region") } else { None };
        let stack = "none";
        let palette = json!([
            "#3164a3", "#779bc9", "#ad9c7f", "#7a8895", "#8a576f",
            "#5c7b5c", "#d49656", "#ae6349", "#609f9e", "#232f3a"
        ]);

        let axis_x = json!({
            "grid": {"visible": false, "threshold": null, "width": 1.0, "color": "#D0D3D9", "opacity": 1.0},
            "ticks": {"color": "#EDEFF2", "visible": false, "count": 6.0},
            "labels": {
                "font": {"family": TEST_FONT, "color": "#626366", "size": 11.0, "weight": "400"},
                "padding": 6.0,
                "min_gap": 4.0,
                "format": null,
                "visible": true
            },
            "title": {"font": {"family": TEST_FONT, "weight": "600"}, "padding": 0.0, "visible": false},
            "position": null,
            "mirror": null,
            "band_position": 0.5,
            "scale": {"padding": 0.0},
            "tick_values": [],
            "domain_max": null,
            "domain_min": null,
            "ruler": null,
            "time_unit": null,
            "fiscal_year_start_month": 1, "is_quantitative": true, "zero_anchored": false,
            "fill": null,
            "line": null
        });

        let axis_y = json!({
            "grid": {
                "visible": true,
                "threshold": {"color": "#8A8C8F", "width": 2.0},
                "width": 1.0,
                "color": "#D0D3D9",
                "opacity": 1.0
            },
            "ticks": {"color": "#D0D3D9", "visible": false, "count": 6.0},
            "labels": {
                "font": {"family": TEST_FONT, "color": "#626366", "size": 11.0, "weight": "400"},
                "padding": 6.0,
                "min_gap": null,
                "format": null,
                "visible": true,
                "align": "right"
            },
            "title": {"font": {"family": TEST_FONT, "weight": "600"}, "padding": 0.0, "visible": false},
            "position": "left",
            "mirror": null,
            "band_position": 0.5,
            "scale": {"padding": 0.0},
            "tick_values": [0.0, 500.0, 1000.0, 1500.0, 2000.0],
            "domain_max": 2000.0,
            "domain_min": null,
            "ruler": null,
            "tick_label": {"format": ",.0~f"},
            "time_unit": null,
            "fiscal_year_start_month": 1, "is_quantitative": true, "zero_anchored": false,
            "fill": null,
            "line": null
        });

        let resolved = json!({
            "link_open": "page",
            "id": "test_vertical",
            "chart_type": "bar",
            "title": "Test Vertical Bar",
            "title_style": {
                "font": {"family": TEST_FONT, "color": "#494A4C", "size": 18.0, "weight": 500.0, "case": "none"},
                "position": {"offset": 10.0}
            },
            "background": "#ffffff",
            "canvas": "#ffffff",
            "x": "product",
            "y": "revenue",
            "color": color_field,
            "orientation": "vertical",
            "stack": stack,
            "palette": palette,
            "multiples": null,
            "field_labels": {}, "wide_measures": [],
            "layers": [],
            "legend": {
                "visible": grouped,
                "position": {"edge": "top", "align": "start", "overlay": false},
                "direction": "horizontal",
                "title": {"font": {"case": "title", "family": TEST_FONT}},
                "label": {
                    "padding": 8.0,
                    "font": {"family": TEST_FONT, "color": "#626366", "size": 11.0, "weight": "400"}
                }
            },
            "style": {
                "axis_x": axis_x,
                "axis_y": axis_y,
                "mark": {
                    "band_width": 0.7,
                    "size": 20.0,
                    "border": {"width": null, "color": null, "radius": 4.0},
                    "labels": {"visible": false}
                },
                "grouped": {"padding_inner": 0.2, "padding_outer": 0.2},
                "single_series_fill": "#3164a3",
                "label_font_size": 11.0,
                "label_usable_ratio": 0.8,
                "series_label": {"font_family": "'Inter'", "font_size": 11.0, "font_weight": "400", "font_style": "normal", "dark_companion_palette": [], "pin_companions": []},
                "endpoint_labels": {"visible": false, "height": 20.0, "label_offset": 6.0}
            }
        });

        json!({
            "resolved": resolved,
            "rows": rows,
            "width": 800.0,
            "height": 500.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": {"left": 16.0, "right": 16.0, "top": 16.0, "bottom": 16.0}
        })
    }

    fn make_payload(payload_json: serde_json::Value) -> Payload {
        crate::test_payload(payload_json)
    }

    /// `all_marks_degenerate`: a vertical bar is never thinner than a quarter
    /// pixel, so only a plot with no height flattens it; a horizontal bar's
    /// length is its width. All-zero data is a faithful blank render, and a
    /// stroked layer keeps a collapsed plot from reading as blank.
    #[test]
    fn collapsed_bars_raise_only_where_the_marks_of_a_measure_all_go_flat() {
        let rows = |values: &[f64]| -> Vec<serde_json::Map<String, Value>> {
            values.iter().map(|v| serde_json::json!({"v": v}).as_object().expect("an object").clone()).collect()
        };
        let collapsed = |horizontal: bool, plot: (f64, f64), stroked: bool, values: &[f64]| {
            collapsed_bars(horizontal, plot, stroked, ("c", 60.0), &rows(values), "v").is_err()
        };
        assert!(collapsed(true, (0.0, 90.0), false, &[3.0]), "a horizontal bar of no length is flat");
        assert!(!collapsed(false, (0.0, 90.0), false, &[3.0]), "a vertical bar keeps its quarter pixel");
        assert!(collapsed(false, (90.0, 0.0), false, &[3.0]), "a vertical bar of no height is flat");
        assert!(!collapsed(true, (0.0, 90.0), false, &[0.0, 0.0]), "all-zero data is a blank render");
        assert!(!collapsed(true, (0.0, 90.0), true, &[3.0]), "a stroked layer still reads");
    }

    /// A `type: line` overlay layer sharing the base's y scale: no
    /// `axis_y.position`, no `x`, no `color`, the base's own query (both
    /// omitted → both `None`). `target` is the layer's own row field.
    fn line_layer_json(stroke_color: &str) -> serde_json::Value {
        serde_json::json!({
            "type": "line",
            "line_mark": {
                "disconnected_width": 0.9,
                "stroke": {"width": 2.0, "color": stroke_color, "cap": "round", "join": "round"},
                "halo_multiplier": 2.0,
                "curve": "linear",
                "labels": {"visible": false}
            },
            "point_mark": {
                "size": 48.0, "color": stroke_color, "filled": false, "fill": "#ffffff",
                "stroke_width": 2.0, "labels": {"visible": false}
            },
            "axis_y": {"position": null},
            "x": null,
            "y": "target",
            "label": null,
            "color": null,
            "ink": stroke_color,
            "query_name": null
        })
    }

    /// The layered-chart palette errors belong to the layered path: a chart
    /// without layers renders on an empty palette or a short one.
    #[test]
    fn a_chart_without_layers_renders_on_an_empty_or_short_palette() {
        let mut empty = make_vertical_payload(&[("Jan", 100.0), ("Feb", 150.0)], true);
        empty["resolved"]["palette"] = serde_json::json!([]);
        render(&make_payload(empty), &test_fonts()).expect("an empty palette renders without layers");
        let mut short = make_vertical_payload(&[("Jan", 100.0), ("Feb", 150.0)], false);
        short["resolved"]["category_colors"] = serde_json::json!([{"field": "other", "slots": {"x": 99}}]);
        render(&make_payload(short), &test_fonts()).expect("an unrelated board slot past the palette renders");
    }

    mod layers {
        use super::*;

        fn payload_with_layer(cats: &[(&str, f64, f64)], layer: serde_json::Value) -> serde_json::Value {
            let mut payload_json = make_vertical_payload(
                &cats.iter().map(|(c, v, _)| (*c, *v)).collect::<Vec<_>>(),
                false,
            );
            payload_json["rows"] = serde_json::json!(cats
                .iter()
                .map(|(c, v, t)| serde_json::json!({"product": c, "revenue": v, "target": t}))
                .collect::<Vec<_>>());
            payload_json["resolved"]["layers"] = serde_json::json!([layer]);
            // Production bakes `legend.visible: true` for a layered chart
            // even off a colorless base (`force_legend_visible` — probed:
            // `column_line_combo_review`'s own resolved payload) — a real
            // payload never reaches the renderer with this still `false`.
            payload_json["resolved"]["legend"]["visible"] = serde_json::json!(true);
            payload_json
        }

        /// A layer's marks are not degenerate, so production renders the chart
        /// and the engine must not claim it paints no marks.
        #[test]
        fn a_zoomed_floor_beside_a_layer_is_not_the_painted_no_marks_code() {
            let mut payload_json =
                payload_with_layer(&[("Jan", 120.0, 130.0), ("Feb", 150.0, 140.0)], line_layer_json("#dea628"));
            let axis_y = &mut payload_json["resolved"]["style"]["axis_y"];
            axis_y["scale"] = serde_json::json!({"continuous": {"zero": false}});
            axis_y["tick_values"] = serde_json::json!([100.0, 150.0, 200.0]);
            axis_y["domain_min"] = serde_json::json!(115.2);
            axis_y["domain_max"] = serde_json::json!(184.8);
            if let Err(err) = render(&make_payload(payload_json), &test_fonts()) {
                assert_ne!(err.code, "ERR-CHART-PAINTED-NO-MARKS", "{err:?}");
            }
        }

        /// A layer with no format of its own reads the base's `labels.format`
        /// literal: its affix on every non-zero tick while the base paints
        /// plain, and the bare spec once the base bakes a `tick_label`
        /// ladder that owns the affix (`axis_to_vl`'s two arms).
        #[test]
        fn a_format_less_layer_axis_wears_the_base_affix_only_off_a_baked_ladder() {
            let render_base = |tick_label: serde_json::Value| {
                let mut j = payload_with_layer(&[("Jan", 1800.0, 90.0)], line_layer_json("#dea628"));
                j["resolved"]["layers"][0]["axis_y"] = serde_json::json!({"position": "right"});
                j["resolved"]["style"]["axis_y"]["tick_label"] = tick_label;
                j["resolved"]["style"]["axis_y"]["labels"]["format"] = serde_json::json!({"spec": ",.0f", "suffix": " CHF"});
                render(&make_payload(j), &test_fonts()).expect("renders")
            };
            let svg = render_base(serde_json::json!({"format": ",.0f"}));
            assert!(svg.contains(">500 CHF</text>") && svg.contains(">20</text>") && !svg.contains(">20 CHF</text>"), "{svg}");
            let svg = render_base(serde_json::json!(null));
            assert!(svg.contains(">500 CHF</text>") && svg.contains(">20 CHF</text>"), "{svg}");
        }

        /// A shared-scale layer draws on the base's scale, whatever floor it takes.
        #[test]
        fn a_negative_floor_with_a_shared_scale_layer_draws() {
            let mut payload_json =
                payload_with_layer(&[("Jan", -30.0, 90.0), ("Feb", 45.0, 40.0)], line_layer_json("#dea628"));
            payload_json["resolved"]["style"]["axis_y"]["tick_values"] =
                serde_json::json!([-40.0, -20.0, 0.0, 20.0, 40.0]);
            payload_json["resolved"]["style"]["axis_y"]["domain_max"] = serde_json::json!(48.6);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert!(svg.contains('\u{2212}'), "the negative rung is labeled: {svg}");
        }

        /// A layer line paints its authored dasharray as written
        /// (`_layers.py`'s shared `emit_line_layer` strokeDash); a list it
        /// cannot read refuses by name instead of drawing solid.
        #[test]
        fn a_line_layer_paints_its_authored_dasharray_and_refuses_an_unreadable_one() {
            let paint = |dasharray: &str| {
                let mut layer = line_layer_json("#dea628");
                layer["line_mark"]["stroke"]["dasharray"] = serde_json::json!(dasharray);
                let payload = make_payload(payload_with_layer(&[("Jan", 100.0, 90.0)], layer));
                render(&payload, &test_fonts())
            };
            let svg = paint("5 4").expect("renders");
            assert!(svg.contains("stroke-dasharray=\"5,4\""), "{svg}");
            let err = paint("5,4").unwrap_err();
            assert!(err.contains("line_mark.stroke.dasharray"), "{err}");
        }

        /// `bar` and `line` are the two modeled layer types — anything else
        /// (`area`/`scatter`/…) is named before a struct deserialize even
        /// runs.
        #[test]
        fn an_unmodeled_layer_type_is_rejected_by_name() {
            let mut layer = line_layer_json("#dea628");
            layer["type"] = serde_json::json!("text");
            let payload = make_payload(payload_with_layer(&[("Jan", 100.0, 90.0)], layer));
            let err = render(&payload, &test_fonts()).unwrap_err();
            assert!(err.contains("layer type \"text\""), "names the layer type: {err}");
        }

        /// A well-formed `type: "bar"` layer payload (`bar_mark`, not
        /// `line_mark`) deserializes into `OverlayBarLayer`, never
        /// `OverlayLineLayer` — dispatched by its own authored `type` before
        /// either struct is tried.
        #[test]
        fn a_bar_type_layer_with_no_line_mark_deserializes_into_its_own_struct() {
            let layer = serde_json::json!({
                "type": "bar",
                "bar_mark": {
                    "border": {"width": 1.0, "color": "#FFFFFF", "radius": 2.0},
                    "band_width": 0.8,
                    "labels": {"visible": false}
                },
                "axis_y": {"position": null},
                "x": "product",
                "y": "target",
                "label": null,
                "color": null,
                "ink": "#779bc9", "query_name": null
            });
            let payload = make_payload(payload_with_layer(&[("Jan", 100.0, 90.0)], layer));
            let svg = render(&payload, &test_fonts())
                .expect("a well-formed bar-type layer sharing the base's own x renders");
            assert!(svg.contains("fill=\"#3164a3\""), "base bar still paints: {svg}");
            assert!(svg.contains("fill=\"#779bc9\""), "layer bar paints at the next palette slot: {svg}");
        }

        pub(super) fn bar_layer_json(query_name: Option<&str>) -> serde_json::Value {
            serde_json::json!({
                "type": "bar",
                "bar_mark": {
                    "border": {"width": 1.0, "color": "#FFFFFF", "radius": 2.0},
                    "band_width": 0.8,
                    "labels": {
                        "visible": true, "position": "middle",
                        "field": null, "format": null, "dx": null, "dy": null, "font": null
                    }
                },
                "axis_y": {"position": null},
                "x": "product",
                "y": "target",
                "label": null,
                "color": null,
                "ink": "#779bc9",
                "query_name": query_name
            })
        }

        /// The task's own probed fixture (`fixtures/layered-bar-labels-x-
        /// order`): a bar base at categories "1"/"3"/"5" with a bar-type
        /// layer reading a DIFFERENT query at "2"/"4"/"6", the same x field
        /// name on both sides. `overlay_x_domain_values`/`rendered_x_domain`
        /// (`x_domain.py`) union the two numeric-string columns into one
        /// ordered domain (`_reconcile_x_domain` pins it on the shared
        /// scale) so the axis draws six bands, not the base's own three;
        /// `emit_bar_layer` paints the layer's own rows at the next
        /// unclaimed palette slot, and its OWN `bar_mark.labels`
        /// (`position: middle`) reuse the base's own value-label painter.
        #[test]
        fn a_bar_type_layer_paints_the_union_x_domain_at_the_next_palette_slot_with_its_own_labels() {
            let mut payload_json =
                make_vertical_payload(&[("1", 120.0), ("3", 150.0), ("5", 180.0)], false);
            payload_json["resolved"]["query_name"] = serde_json::json!("actual");
            payload_json["resolved"]["style"]["mark"]["labels"] = serde_json::json!({
                "visible": true, "position": "middle",
                "field": null, "format": null, "dx": null, "dy": null, "font": null
            });
            payload_json["resolved"]["layers"] = serde_json::json!([bar_layer_json(Some("projected"))]);
            payload_json["resolved"]["legend"]["visible"] = serde_json::json!(true);
            payload_json["datasets"] = serde_json::json!({
                "projected": [
                    {"product": "2", "target": 140.0},
                    {"product": "4", "target": 170.0},
                    {"product": "6", "target": 200.0}
                ]
            });
            let svg = render(&make_payload(payload_json), &test_fonts())
                .expect("bar-type layer over the union x domain renders");

            assert_eq!(
                svg.matches("Z\" fill=\"#3164a3\"/>").count(),
                3,
                "three base bars at palette[0]: {svg}"
            );
            assert_eq!(
                svg.matches("fill=\"#779bc9\" stroke=\"#FFFFFF\"").count(),
                3,
                "three layer bars at the next palette slot: {svg}"
            );

            // "middle" position labels: half the value, white text
            // (background) since middle sits inside the bar.
            for label in [">60<", ">75<", ">90<", ">70<", ">85<", ">100<"] {
                assert!(!svg.contains(label), "labels show the REAL value, not half of it: {svg}");
            }
            for label in ["120", "150", "180", "140", "170", "200"] {
                let needle = format!("fill=\"#ffffff\">{label}<");
                assert!(svg.contains(&needle), "value label {label} present in white: {svg}");
            }

            // Legend: base ("revenue") and layer ("target") resolve to
            // DIFFERENT default labels here, so two distinct entries — the
            // dedup-on-equal-label case is pinned by the sibling test below.
            assert!(svg.contains(">Revenue<"), "base legend label: {svg}");
            assert!(svg.contains(">Target<"), "layer legend label: {svg}");
        }

        /// The probed fixture's own field-name coincidence: base y="tickets"
        /// and the (unauthored-label) layer's y is ALSO "tickets" — same
        /// default legend text on both sides. Vega's ordinal color scale
        /// dedupes a repeated domain value to its FIRST occurrence (d3-
        /// scale's own `domain()` setter skips a value it has already
        /// indexed), so the compiled legend shows exactly one swatch, in
        /// the BASE's own fill — probed directly against `fixtures/layered-
        /// bar-labels-x-order`'s reference SVG (`aria-label="Symbol legend
        /// for fill color with 1 value: tickets"`).
        #[test]
        fn a_bar_type_layer_sharing_the_bases_own_label_collapses_to_one_legend_entry() {
            let mut payload_json =
                make_vertical_payload(&[("1", 120.0), ("3", 150.0), ("5", 180.0)], false);
            payload_json["resolved"]["y"] = serde_json::json!("tickets");
            payload_json["rows"] = serde_json::json!([
                {"product": "1", "tickets": 120.0},
                {"product": "3", "tickets": 150.0},
                {"product": "5", "tickets": 180.0}
            ]);
            payload_json["resolved"]["query_name"] = serde_json::json!("actual");
            let mut layer = bar_layer_json(Some("projected"));
            layer["y"] = serde_json::json!("tickets");
            payload_json["resolved"]["layers"] = serde_json::json!([layer]);
            payload_json["resolved"]["legend"]["visible"] = serde_json::json!(true);
            payload_json["datasets"] = serde_json::json!({
                "projected": [
                    {"product": "2", "tickets": 140.0},
                    {"product": "4", "tickets": 170.0},
                    {"product": "6", "tickets": 200.0}
                ]
            });
            let svg = render(&make_payload(payload_json), &test_fonts())
                .expect("bar-type layer sharing the base's own label renders");
            assert_eq!(svg.matches(">Tickets<").count(), 1, "legend collapses to one entry: {svg}");
            assert!(
                svg.contains("fill=\"#3164a3\" stroke=\"none\""),
                "the single swatch keeps the base's own fill and border: {svg}"
            );
        }

        /// A one-category base under bar layers grouped by `region`, one dataset per layer.
        fn own_color_layers_svg(datasets: &[&[(&str, f64)]]) -> Result<String, crate::diagnostic::Diagnostic> {
            own_color_layers_with(datasets, |_| {})
        }

        fn own_color_layers_with(
            datasets: &[&[(&str, f64)]],
            edit: impl FnOnce(&mut serde_json::Value),
        ) -> Result<String, crate::diagnostic::Diagnostic> {
            let mut payload_json = make_vertical_payload(&[("1", 120.0), ("3", 150.0)], false);
            payload_json["resolved"]["query_name"] = serde_json::json!("actual");
            let mut layers = Vec::new();
            let mut sets = serde_json::Map::new();
            for (i, rows) in datasets.iter().enumerate() {
                let name = format!("layer{i}");
                let mut layer = bar_layer_json(Some(&name));
                layer["color"] = serde_json::json!("region");
                layers.push(layer);
                sets.insert(
                    name,
                    rows.iter()
                        .map(|(region, target)| serde_json::json!({"product": "1", "target": target, "region": region}))
                        .collect(),
                );
            }
            payload_json["resolved"]["layers"] = serde_json::json!(layers);
            payload_json["resolved"]["legend"]["visible"] = serde_json::json!(true);
            payload_json["datasets"] = sets.into();
            edit(&mut payload_json);
            bake_series_inks(&mut payload_json);
            render(&make_payload(payload_json), &test_fonts())
        }

        /// What resolve bakes for layers grouped by `region`: the base's single
        /// fill holds slot 0, each value not yet on the scale takes the next slot.
        fn bake_series_inks(payload_json: &mut serde_json::Value) {
            let slots = ["#779bc9", "#ad9c7f", "#7a8895", "#8a576f"];
            let mut claimed: Vec<String> = Vec::new();
            let datasets = payload_json["datasets"].clone();
            for (i, layer) in payload_json["resolved"]["layers"].as_array_mut().expect("layers").iter_mut().enumerate() {
                let mut inks = serde_json::Map::new();
                let mut values: Vec<&str> = datasets[format!("layer{i}")]
                    .as_array()
                    .expect("rows")
                    .iter()
                    .filter_map(|row| row["region"].as_str())
                    .collect();
                values.sort();
                values.dedup();
                for value in values {
                    if !claimed.iter().any(|c| c == value) {
                        claimed.push(value.to_string());
                    }
                    let slot = claimed.iter().position(|c| c == value).expect("claimed");
                    inks.insert(value.to_string(), serde_json::json!(slots[slot]));
                }
                layer["ink"] = serde_json::Value::Null;
                layer["series_inks"] = inks.into();
            }
        }

        /// Production drops a null color value from the scale's domain but still
        /// draws the row: a series the scale has no color for, unfilled, and sorting
        /// lowest so it stacks on top of the colored ones.
        #[test]
        fn a_null_color_value_on_a_bar_layer_is_an_unpainted_series_above_the_colored_ones() {
            let rows: &[(&str, f64)] = &[("East", 40.0), ("West", 30.0)];
            let svg = own_color_layers_with(&[rows], |p| p["datasets"]["layer0"][0]["region"] = serde_json::Value::Null)
                .expect("renders");
            assert!(svg.contains("fill=\"#779bc9\" stroke=\"#FFFFFF\""), "West takes palette[1], the null claims none: {svg}");
            assert!(!svg.contains("#ad9c7f"), "no second slot is claimed: {svg}");
            assert_eq!(svg.matches(">West</text>").count(), 1, "{svg}");
            let colored = svg.find("fill=\"#779bc9\" stroke=\"#FFFFFF\"").expect("West's segment");
            let unpainted = svg.find("fill=\"none\" stroke=\"#FFFFFF\"").expect("the null segment");
            assert!(colored < unpainted, "the null segment stacks last: {svg}");
        }

        /// A color promotes the series into each datum's description only where two
        /// non-null values distinguish the rows (`series_row_promoted`): `West` beside a null is
        /// one series, a second value names every series, a null one `null`.
        #[test]
        fn a_bar_layer_names_the_series_of_its_data_only_where_two_values_distinguish_them() {
            let unnamed = own_color_layers_with(&[&[("East", 40.0), ("West", 30.0)]], |p| {
                p["datasets"]["layer0"][0]["region"] = serde_json::Value::Null;
                p["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.1f");
            })
            .expect("renders");
            assert!(!unnamed.contains("target: West") && !unnamed.contains("target: null"), "{unnamed}");
            let named = own_color_layers_with(&[&[("East", 40.0), ("West", 30.0), ("North", 20.0)]], |p| {
                p["datasets"]["layer0"][0]["region"] = serde_json::Value::Null;
                p["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.1f");
            })
            .expect("renders");
            assert!(named.contains("Target: null") && named.contains("Target: West"), "{named}");
        }

        #[test]
        fn a_scatter_layer_with_its_own_color_over_a_horizontal_bar_names_each_value() {
            let mut scatter = scatter_layer_json(false);
            scatter["color"] = serde_json::json!("product");
            scatter["series_inks"] = serde_json::json!({"Jan": "#779bc9", "Feb": "#ad9c7f"});
            let mut payload_json = horizontal_layer_payload(scatter);
            payload_json["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.0f");
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert!(svg.contains("Target: Jan; Target: 90") && svg.contains("Target: Feb; Target: 140"), "series rows: {svg}");
            assert!(svg.contains("d=\"M5,0A5,5,0,1,1,-5,0A5,5,0,1,1,5,0\""), "circle legend glyphs: {svg}");
        }

        #[test]
        fn a_grouped_bar_layer_keeps_square_legend_glyphs_beside_a_scatter_layer() {
            let mut bar = bar_layer_json(None);
            bar["color"] = serde_json::json!("product");
            bar["series_inks"] = serde_json::json!({"Jan": "#779bc9", "Feb": "#ad9c7f"});
            let mut payload_json = horizontal_layer_payload(bar);
            payload_json["resolved"]["layers"] = serde_json::json!([payload_json["resolved"]["layers"][0].clone(), scatter_layer_json(false)]);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert!(!svg.contains("d=\"M5,0A5,5,0,1,1,-5,0A5,5,0,1,1,5,0\""), "no circle glyph for a bar layer's values: {svg}");
        }

        #[test]
        fn a_null_color_value_on_a_line_layer_is_a_series_with_no_stroke() {
            let mut layer = line_layer_json("#cc0000");
            layer["line_mark"]["stroke"]["color"] = serde_json::Value::Null;
            layer["point_mark"]["color"] = serde_json::Value::Null;
            layer["color"] = serde_json::json!("region");
            layer["ink"] = serde_json::Value::Null;
            layer["series_inks"] = serde_json::json!({"West": "#779bc9"});
            layer["query_name"] = serde_json::json!("regional");
            let mut payload_json = payload_with_layer(&[("Jan", 100.0, 90.0)], layer);
            payload_json["resolved"]["query_name"] = serde_json::json!("base");
            payload_json["datasets"] = serde_json::json!({"regional": [
                {"product": "Jan", "target": 50.0, "region": null},
                {"product": "Jan", "target": 60.0, "region": "West"},
            ]});
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert!(svg.contains("stroke=\"none\" stroke-width"), "the null group strokes nothing: {svg}");
            assert_eq!(svg.matches(">West</text>").count(), 1, "one legend entry, none for the null: {svg}");
        }

        /// The points paint the color the layer claimed even when its label repeats
        /// the base's: production fills them with that literal.
        #[test]
        fn a_scatter_layer_labeled_like_the_base_paints_its_claimed_color() {
            let mut scatter = scatter_layer_json(false);
            scatter["point_mark"]["color"] = serde_json::Value::Null;
            scatter["ink"] = serde_json::json!("#779bc9");
            scatter["y"] = serde_json::json!("revenue");
            let svg = render(&make_payload(payload_with_layer(&[("Jan", 100.0, 40.0)], scatter)), &test_fonts()).expect("renders");
            assert!(svg.contains("<circle transform=\"translate(") && svg.contains("fill=\"#779bc9\""), "{svg}");
        }

        #[test]
        fn a_bar_type_layer_with_a_color_field_paints_each_value_at_its_claimed_slot() {
            let svg = own_color_layers_svg(&[&[("East", 40.0), ("West", 30.0)]]).expect("renders");
            assert_eq!(svg.matches(">East</text>").count(), 1, "{svg}");
            assert!(svg.contains("fill=\"#779bc9\" stroke=\"#FFFFFF\""), "East takes palette[1]: {svg}");
            assert!(svg.contains("fill=\"#ad9c7f\" stroke=\"#FFFFFF\""), "West takes palette[2]: {svg}");
        }

        /// No `order` channel over a colorless base: Vega-Lite stacks by the color
        /// field descending, whatever the value sums.
        #[test]
        fn a_color_grouped_bar_layer_stacks_by_its_color_field_not_by_value() {
            let svg = own_color_layers_svg(&[&[("East", 60.0), ("West", 20.0)]]).expect("renders");
            let at = |fill: &str| svg.find(&format!("fill=\"{fill}\" stroke=\"#FFFFFF\"")).expect(fill);
            assert!(at("#ad9c7f") < at("#779bc9"), "West (palette[2]) stacks first, East on top: {svg}");
        }

        #[test]
        fn a_stacked_bar_layer_total_past_the_value_domain_draws_unclipped() {
            let within = own_color_layers_svg(&[&[("East", 40.0), ("West", 30.0)]]).expect("renders");
            let past = own_color_layers_svg(&[&[("East", 1500.0), ("West", 1500.0)]]).expect("renders");
            assert!(past.contains("V -"), "the stack's tip sits above the plot: {past}");
            assert!(!within.contains("V -"), "{within}");
        }

        /// `_overlay.py`: a value already on the shared scale keeps its slot, so
        /// a second layer reuses it and the legend lists it once.
        #[test]
        fn a_color_value_shared_by_two_bar_layers_reuses_its_slot() {
            let svg = own_color_layers_svg(&[&[("East", 40.0), ("West", 30.0)], &[("East", 20.0), ("North", 10.0)]])
                .expect("renders");
            assert_eq!(svg.matches(">East</text>").count(), 1, "{svg}");
            assert_eq!(svg.matches("fill=\"#779bc9\" stroke=\"#FFFFFF\"").count(), 2, "East bars share palette[1]: {svg}");
            assert!(svg.contains("fill=\"#7a8895\" stroke=\"#FFFFFF\""), "North is the next free color: {svg}");
        }

        #[test]
        fn a_pinned_bar_layer_with_its_own_color_grouping_paints_its_values_on_its_own_axis() {
            let mut layer = bar_layer_json(None);
            layer["axis_y"]["position"] = serde_json::json!("right");
            layer["color"] = serde_json::json!("product");
            layer["ink"] = serde_json::Value::Null;
            layer["series_inks"] = serde_json::json!({"Jan": "#779bc9"});
            let svg = render(&make_payload(payload_with_layer(&[("Jan", 1000.0, 3.1)], layer)), &test_fonts()).expect("renders");
            assert!(svg.contains("fill=\"#779bc9\" stroke=\"#FFFFFF\""), "Jan takes palette[1]: {svg}");
            assert_eq!(svg.matches(">Jan</text>").count(), 2, "the category label and the legend entry: {svg}");
        }

        /// A bar-type layer pinned to a side owns an independent scale: its own
        /// bars span its own domain, and the axis it pins takes its own ladder.
        #[test]
        fn a_pinned_bar_layer_gets_its_own_axis_on_the_side_it_pins() {
            for side in ["right", "left"] {
                let mut layer = bar_layer_json(None);
                layer["axis_y"]["position"] = serde_json::json!(side);
                layer["bar_mark"]["labels"]["visible"] = serde_json::json!(false);
                let svg = render(
                    &make_payload(payload_with_layer(&[("Jan", 1000.0, 3.1), ("Feb", 1500.0, 4.2)], layer)),
                    &test_fonts(),
                )
                .expect("renders");
                assert!(svg.contains(">4</text>") && svg.contains(">3</text>"), "{side}: its own 0..4.5 ladder: {svg}");
            }
        }

        #[test]
        fn a_bar_type_layer_with_its_own_x_field_widens_the_band_domain_by_that_column() {
            let mut layer = bar_layer_json(Some("projected"));
            layer["x"] = serde_json::json!("other_field");
            let mut payload_json = make_vertical_payload(&[("1", 120.0)], false);
            payload_json["resolved"]["query_name"] = serde_json::json!("actual");
            payload_json["resolved"]["layers"] = serde_json::json!([layer]);
            payload_json["resolved"]["legend"]["visible"] = serde_json::json!(true);
            payload_json["datasets"] =
                serde_json::json!({"projected": [{"other_field": "2", "target": 140.0}]});
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert!(svg.find(">1<").unwrap() < svg.find(">2<").unwrap(), "the layer's own x joins the axis: {svg}");
        }

        #[test]
        fn a_bar_type_layer_over_a_grouped_base_renders() {
            let layer = bar_layer_json(Some("projected"));
            let mut payload_json = make_vertical_payload(&[("1", 120.0)], true);
            payload_json["resolved"]["query_name"] = serde_json::json!("actual");
            payload_json["resolved"]["layers"] = serde_json::json!([layer]);
            payload_json["datasets"] =
                serde_json::json!({"projected": [{"product": "2", "target": 140.0}]});
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert!(svg.contains(">2<"), "the layer's category joins the axis: {svg}");
        }

        #[test]
        fn a_bar_type_layer_x_union_with_no_stated_order_stays_in_paint_order() {
            let layer = bar_layer_json(Some("projected"));
            let mut payload_json = make_vertical_payload(&[("Jan", 120.0)], false);
            payload_json["resolved"]["query_name"] = serde_json::json!("actual");
            payload_json["resolved"]["layers"] = serde_json::json!([layer]);
            payload_json["resolved"]["legend"]["visible"] = serde_json::json!(true);
            payload_json["datasets"] =
                serde_json::json!({"projected": [{"product": "Feb", "target": 140.0}]});
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert!(svg.find(">Jan<").unwrap() < svg.find(">Feb<").unwrap(), "{svg}");
        }

        /// A layer's own color grouping over a grouped base claims its values on
        /// the scale the base's own series already seeded: the next free slots.
        #[test]
        fn a_layer_color_grouping_over_a_grouped_base_claims_its_values_after_the_bases() {
            let mut layer = line_layer_json("#dea628");
            layer["color"] = serde_json::json!("region");
            layer["ink"] = serde_json::Value::Null;
            layer["series_inks"] = serde_json::json!({"East": "#ad9c7f"});
            let mut payload_json = payload_with_layer(&[("Jan", 100.0, 90.0)], layer);
            payload_json["rows"] = serde_json::json!([
                {"product": "Jan", "segment": "Enterprise", "revenue": 100.0, "target": 90.0, "region": "East"},
                {"product": "Jan", "segment": "Mid-Market", "revenue": 40.0, "target": 90.0, "region": "East"},
            ]);
            payload_json["resolved"]["color"] = serde_json::json!("segment");
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert_eq!(svg.matches(">East</text>").count(), 1, "the layer's value is a legend entry: {svg}");
            assert!(svg.contains("stroke=\"#ad9c7f\" stroke-width"), "East takes palette[2], past the base's two: {svg}");
        }

        #[test]
        fn a_line_layer_with_its_own_x_is_placed_on_that_column() {
            let mut layer = line_layer_json("#dea628");
            layer["x"] = serde_json::json!("other_field");
            let mut payload_json = payload_with_layer(&[("Jan", 100.0, 90.0)], layer);
            payload_json["rows"][0]["other_field"] = serde_json::json!("Feb");
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert!(svg.find(">Jan<").unwrap() < svg.find(">Feb<").unwrap(), "the layer's own x joins the axis: {svg}");
        }

        /// A cross-query layer is admitted when its dataset is present
        /// (see `a_cross_query_layer_with_no_color_reads_its_own_datasets_rows`
        /// below) — but the harness/board can still hand a layer a
        /// `query_name` with no matching entry in `payload.datasets` at
        /// all, which must name the missing dataset rather than silently
        /// falling back to the base's own rows.
        #[test]
        fn a_layer_reading_a_different_query_with_no_dataset_is_rejected_by_name() {
            let mut layer = line_layer_json("#dea628");
            layer["query_name"] = serde_json::json!("other_query");
            let mut payload_json = payload_with_layer(&[("Jan", 100.0, 90.0)], layer);
            payload_json["resolved"]["query_name"] = serde_json::json!("base_query");
            let payload = make_payload(payload_json);
            let err = render(&payload, &test_fonts()).unwrap_err();
            assert!(err.contains("no dataset"), "names the unmodeled shape: {err}");
        }

        #[test]
        fn a_line_layer_with_value_labels_prints_each_value_above_its_point() {
            let mut layer = line_layer_json("#dea628");
            layer["line_mark"]["labels"]["visible"] = serde_json::json!(true);
            let payload = make_payload(payload_with_layer(
                &[("Jan", 100.0, 41.5), ("Feb", 120.0, 63.5)],
                layer,
            ));
            let svg = render(&payload, &test_fonts()).expect("renders");
            assert!(svg.contains(">41.5</text>") && svg.contains(">63.5</text>"), "{svg}");
        }

        /// A right value axis rides the rail concat, which reserves a layer's room beside the rail.
        #[test]
        fn a_layer_beside_a_rail_with_a_right_value_axis_renders() {
            let mut payload_json = make_rail_payload();
            payload_json["resolved"]["layers"] = serde_json::json!([line_layer_json("#dea628")]);
            payload_json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("right");
            payload_json["resolved"]["style"]["axis_y"]["labels"]["align"] = serde_json::json!("right");
            for row in payload_json["rows"].as_array_mut().expect("rows") {
                row["target"] = serde_json::json!(1.0);
            }
            render(&make_payload(payload_json), &test_fonts()).expect("renders");
        }

        #[test]
        fn a_layer_value_label_prints_another_field() {
            let mut layer = line_layer_json("#dea628");
            layer["line_mark"]["labels"]["visible"] = serde_json::json!(true);
            layer["line_mark"]["labels"]["field"] = serde_json::json!("revenue");
            let payload = make_payload(payload_with_layer(&[("Jan", 137.5, 90.0)], layer));
            let svg = render(&payload, &test_fonts()).expect("renders");
            assert!(svg.contains(">137.5</text>"), "{svg}");
        }

        /// A left pin beside an unpinned layer resolves to `[left, right]`, a mixed
        /// set, so the base stays left (`_overlay.py`'s `base_side`) and the
        /// unpinned layer, off the base's side, nests the zero rule.
        #[test]
        fn a_left_pin_beside_an_unpinned_layer_leaves_the_base_left_and_the_rule_on_the_unpinned_one() {
            let mut line = line_layer_json("#dea628");
            line["axis_y"]["position"] = serde_json::json!("left");
            let mut payload_json = payload_with_layer(&[("Jan", 1000.0, 3.1), ("Feb", 1500.0, 4.2)], line.clone());
            payload_json["resolved"]["layers"] = serde_json::json!([line, bar_layer_json(None)]);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            let origin_x: f64 = svg
                .split("<g transform=\"translate(")
                .nth(1)
                .and_then(|r| r.split(',').next())
                .expect("plot origin")
                .parse()
                .expect("number");
            let base_label = svg
                .split("<text ")
                .find(|t| t.contains(">1,500</text>") && t.contains("text-anchor=\"end\""))
                .expect("the base's labels end-anchor on a left axis");
            let x: f64 = base_label.split("x=\"").nth(1).and_then(|r| r.split('"').next()).expect("x").parse().expect("number");
            assert!(x < origin_x, "base labels left of the plot: {svg}");
            assert_eq!(svg.matches("stroke=\"#8A8C8F\"").count(), 2, "the base's rule and the unpinned layer's: {svg}");
        }

        /// A layer pinned left takes the left edge and re-pins the base to the
        /// right, the mirror of the right-pinned shape.
        #[test]
        fn a_left_pinned_layer_takes_the_left_edge_and_the_base_the_right() {
            let mut layer = line_layer_json("#dea628");
            layer["axis_y"]["position"] = serde_json::json!("left");
            layer["axis_y"]["title"] = serde_json::json!("Conversion (%)");
            let svg = render(
                &make_payload(payload_with_layer(&[("Jan", 1000.0, 3.1), ("Feb", 1500.0, 4.2)], layer)),
                &test_fonts(),
            )
            .expect("renders");
            let plot_w = {
                let (x1, x2, _) = *horizontal_lines(&svg).first().expect("a gridline");
                x2 - x1
            };
            // The layer's own ladder (0..4.5) end-anchors left of the plot; the
            // base's (0..1600) sits right of it.
            let anchor_x = |label: &str, anchor: &str| -> f64 {
                let tag = svg
                    .split("<text ")
                    .find(|t| t.contains(&format!(">{label}</text>")) && t.contains(&format!("text-anchor=\"{anchor}\"")))
                    .unwrap_or_else(|| panic!("{label} {anchor}: {svg}"));
                tag.split("x=\"").nth(1).and_then(|r| r.split('"').next()).expect("x").parse().expect("number")
            };
            let origin_x: f64 = svg
                .split("<g transform=\"translate(")
                .nth(1)
                .and_then(|r| r.split(',').next())
                .expect("plot origin")
                .parse()
                .expect("number");
            assert!(anchor_x("4", "end") < origin_x, "layer labels left of the plot");
            assert!(anchor_x("1,500", "end") > origin_x + plot_w, "base labels right of the plot");
        }

        #[test]
        fn a_right_pinned_scatter_layer_gets_its_own_axis() {
            let mut layer = scatter_layer_json(false);
            layer["axis_y"]["position"] = serde_json::json!("right");
            let payload = make_payload(payload_with_layer(&[("Jan", 1000.0, 3.1), ("Feb", 1500.0, 4.2)], layer));
            let svg = render(&payload, &test_fonts()).expect("renders");
            assert!(svg.contains(">4</text>") && svg.contains(">3</text>"), "{svg}");
        }

        /// Once a layer pins a side every layer owns a scale and an axis
        /// (`_resolved_layer_y_orients`): the unpinned bar layer beside it draws its
        /// own, on the free side the base also takes.
        #[test]
        fn a_right_pinned_scatter_layer_beside_a_bar_layer_gives_each_its_own_axis() {
            let mut scatter = scatter_layer_json(false);
            scatter["axis_y"]["position"] = serde_json::json!("right");
            let mut payload_json = payload_with_layer(&[("Jan", 1000.0, 3.1)], scatter.clone());
            payload_json["resolved"]["layers"] = serde_json::json!([bar_layer_json(None), scatter]);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert_eq!(svg.matches(" data-dct-role=\"y-axis\"").count(), 3, "base and both layers: {svg}");
        }

        /// An `area` layer as production resolves it: an edge line with a stroke,
        /// halo and labels only (no `curve`, `connect` or `disconnected_width`).
        fn area_layer_json(stroke_color: Option<&str>) -> serde_json::Value {
            serde_json::json!({
                "type": "area",
                "area_mark": {"opacity": 0.16, "curve": null, "backdrop": false},
                "line_mark": {
                    "stroke": {"width": 2.0, "color": stroke_color, "cap": null, "join": null, "dasharray": null},
                    "halo_multiplier": 2.0,
                    "labels": {"visible": false}
                },
                "point_mark": {"size": null, "color": null, "filled": true, "fill": null, "stroke_width": 2.0, "labels": {"visible": false}},
                "axis_y": {"position": null},
                "x": null, "y": "target", "label": null, "color": null, "ink": "#779bc9", "query_name": null
            })
        }

        fn area_svg(layer: serde_json::Value) -> Result<String, crate::diagnostic::Diagnostic> {
            render(&make_payload(payload_with_layer(&[("Jan", 100.0, 40.0), ("Feb", 120.0, 60.0)], layer)), &test_fonts())
        }

        /// The fill takes the palette slot, closes down to the value zero, and
        /// paints before the edge line, which takes the slot too.
        #[test]
        fn an_area_layer_paints_a_translucent_fill_under_its_line() {
            let svg = area_svg(area_layer_json(None)).expect("renders");
            let area_at = svg.find("mark-area").expect("an area mark");
            let line_at = svg[area_at..].find("mark-line").expect("the edge line follows") + area_at;
            let area = &svg[area_at..line_at];
            assert!(area.contains("fill=\"#779bc9\" fill-opacity=\"0.16\""), "{area}");
            let zero = svg.find("stroke=\"#8A8C8F\"").expect("the zero rule");
            let rule = &svg[svg[..zero].rfind("<line").expect("a line")..zero];
            let y = rule.split("y1=\"").nth(1).and_then(|r| r.split('"').next()).expect("rule y");
            let y: f64 = y.parse().expect("number");
            assert!(area.contains(&format!(",{y:.0}Z")) || area.contains(&format!(",{y}Z")), "baseline {y}: {area}");
            assert!(svg[line_at..].contains("stroke=\"#779bc9\""), "{svg}");
        }

        /// An authored stroke colors the edge line only; the fill is the palette's.
        /// d3's area generator closes a one-point run as `Mx,yLx,bZ`.
        #[test]
        fn a_one_point_area_layer_closes_the_way_d3_does() {
            let svg = render(&make_payload(payload_with_layer(&[("Jan", 100.0, 40.0)], area_layer_json(None))), &test_fonts())
                .expect("renders");
            let area = &svg[svg.find("mark-area").expect("an area mark")..];
            let d = area.split("d=\"").nth(1).and_then(|r| r.split('"').next()).expect("the area path");
            assert!(d.starts_with('M') && d.ends_with('Z') && !d.contains("ZL"), "{d}");
        }

        #[test]
        fn an_authored_stroke_colors_an_area_edge_line_and_not_its_fill() {
            let svg = area_svg(area_layer_json(Some("#cc0000"))).expect("renders");
            assert!(svg.contains("fill=\"#779bc9\" fill-opacity=\"0.16\""), "{svg}");
            assert!(svg.contains("stroke=\"#cc0000\" stroke-width"), "{svg}");
            assert!(!svg.contains("fill=\"#cc0000\""), "no fill or legend swatch takes the stroke: {svg}");
        }

        fn three_category_area_svg(layer: serde_json::Value) -> String {
            let rows = [("Jan", 100.0, 40.0), ("Feb", 120.0, 60.0), ("Mar", 90.0, 30.0)];
            render(&make_payload(payload_with_layer(&rows, layer)), &test_fonts()).expect("renders")
        }

        #[test]
        fn an_area_layer_with_a_monotone_curve_curves_its_fill_and_its_edge_line() {
            let mut layer = area_layer_json(None);
            layer["area_mark"]["curve"] = serde_json::json!("monotone");
            let svg = three_category_area_svg(layer);
            let area_at = svg.find("mark-area").expect("an area mark");
            let line_at = svg[area_at..].find("mark-line").expect("the edge line follows") + area_at;
            assert!(svg[area_at..line_at].contains('C'), "curved fill: {svg}");
            assert!(svg[line_at..].split("/>").next().unwrap().contains('C'), "curved edge: {svg}");
        }

        /// A literal `step` on a band x doubles each row onto its band's two edges and
        /// draws them in row order through `step-after`: the plateaus run to the plot's
        /// own edges and a riser stands on every shared band edge.
        #[test]
        fn a_connected_step_layer_runs_edge_to_edge_in_row_order() {
            let mut layer = line_layer_json("#dea628");
            layer["line_mark"]["curve"] = serde_json::json!("step");
            let rows = [("Jan", 100.0, 40.0), ("Feb", 120.0, 60.0), ("Mar", 90.0, 30.0)];
            let svg = render(&make_payload(payload_with_layer(&rows, layer)), &test_fonts()).expect("renders");
            let line_at = svg.find("mark-line").expect("the layer line");
            let d = svg[line_at..].split("d=\"").nth(1).unwrap().split('"').next().unwrap();
            assert!(d.starts_with("M0,"), "starts at the plot's left edge: {d}");
            let xs: Vec<f64> = d
                .split(['M', 'L'])
                .filter(|v| !v.is_empty())
                .map(|pair| pair.split(',').next().unwrap().parse().unwrap())
                .collect();
            let right = xs.iter().copied().fold(0.0_f64, f64::max);
            for x in &xs {
                let bands = x / right * 3.0;
                assert!((bands - bands.round()).abs() < 1e-3, "off a band edge: {xs:?}");
            }
            assert_eq!(xs.len(), 1 + 2 * 5, "d3 step-after draws two vertices per later one of the six doubled rows: {d}");
        }

        /// `apply_step_band` rejects a data-driven color on a step layer over a band x.
        #[test]
        fn a_colored_step_layer_over_a_band_x_is_rejected_by_name() {
            let mut layer = line_layer_json("#dea628");
            layer["line_mark"]["curve"] = serde_json::json!("step");
            layer["color"] = serde_json::json!("region");
            let rows = [("Jan", 100.0, 40.0), ("Feb", 120.0, 60.0)];
            let mut payload_json = payload_with_layer(&rows, layer);
            for row in payload_json["rows"].as_array_mut().unwrap() {
                row["region"] = serde_json::json!("North");
            }
            let err = crate::families::cartesian_errors::check(&make_payload(payload_json), "bar").unwrap_err();
            assert!(err.to_string().contains(crate::codes::ERR_STEP_BAND_MULTI_SERIES.id), "{err}");
        }

        /// An area layer's edge line adds no ink to the fit (production's bounds stop at
        /// its fill), so a step area layer lays out exactly like a linear one.
        #[test]
        fn a_step_area_layer_adds_no_stroke_ink_to_the_plot_fit() {
            let rows = [("Jan", 100.0, 40.0), ("Feb", 120.0, 60.0), ("Mar", 90.0, 30.0)];
            let frame = |curve: &str| {
                let mut layer = area_layer_json(None);
                layer["area_mark"]["curve"] = serde_json::json!(curve);
                layer["area_mark"]["stroke"] = serde_json::json!({"width": 40.0, "color": null, "cap": "butt", "join": "miter"});
                layer["line_mark"]["stroke"]["width"] = serde_json::json!(40.0);
                let svg = render(&make_payload(payload_with_layer(&rows, layer)), &test_fonts()).expect("renders");
                svg.split("<g transform=\"translate(").nth(1).unwrap().split(')').next().unwrap().to_string()
            };
            assert_eq!(frame("step"), frame("linear"));
        }

        /// A layer over a continuous time x draws its vertices where its data sits: on each instant, the bars'
        /// centers.
        #[test]
        fn a_line_layer_over_a_continuous_time_x_vertexes_on_its_instants() {
            let _locale = crate::time_locale::en_us_scope();
            let rows = [("2020-01-05", 10.0, 5.0), ("2020-01-06", 20.0, 6.0), ("2023-08-29", 30.0, 7.0), ("2023-08-30", 25.0, 8.0)];
            let mut payload_json = payload_with_layer(&rows, line_layer_json("#dea628"));
            payload_json["resolved"]["style"]["axis_x"]["fill"] = serde_json::json!("null");
            payload_json["resolved"]["style"]["axis_x"]["labels"]["overlap"] = serde_json::json!({"skip": true, "tilt": true});
            payload_json["resolved"]["style"]["axis_x"]["ticks"] = serde_json::json!({"color": "#DFE1E5", "visible": false, "count": null});
            payload_json["resolved"]["style"]["label_usable_ratio"] = serde_json::json!(0.8);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            let tag = svg.split("<path ").find(|tag| tag.contains("stroke=\"#dea628\"") && tag.contains("fill=\"none\"")).expect("the layer's path");
            let first_vertex_x: f64 = tag.split("d=\"M").nth(1).and_then(|d| d.split(',').next()).expect("a vertex").trim().parse().expect("a number");
            let bars = bar_extents(&svg);
            assert_eq!(bars.len(), 4);
            let bar_center = bars[0].0 + bars[0].1 / 2.0;
            assert!((first_vertex_x - bar_center).abs() < 1.0, "vertex {first_vertex_x} on the first bar's center {bar_center}");
        }

        /// The y of the last vertex of the area path painted in `fill`.
        fn area_closing_y(svg: &str, fill: &str) -> f64 {
            let at = svg.find(&format!("fill=\"{fill}\" fill-opacity=\"0.16\"")).expect(fill);
            let start = svg[..at].rfind("d=\"").expect("a path") + 3;
            let d = svg[start..at].split('"').next().expect("d");
            d.trim_end_matches('Z').rsplit(',').next().expect("a vertex").parse().expect("a number")
        }

        /// An area layer's color groups stack where they share a category: the first
        /// value on top, the rest closing down onto the one below instead of the zero.
        #[test]
        fn an_area_layer_with_its_own_color_stacks_its_groups() {
            let mut layer = area_layer_json(None);
            layer["color"] = serde_json::json!("region");
            layer["ink"] = serde_json::Value::Null;
            layer["series_inks"] = serde_json::json!({"a": "#779bc9", "b": "#ad9c7f"});
            layer["query_name"] = serde_json::json!("segs");
            let mut payload_json = payload_with_layer(&[("Jan", 100.0, 40.0), ("Feb", 120.0, 60.0)], layer);
            payload_json["resolved"]["query_name"] = serde_json::json!("base");
            payload_json["datasets"] = serde_json::json!({"segs": [
                {"product": "Jan", "target": 40.0, "region": "a"},
                {"product": "Jan", "target": 20.0, "region": "b"},
                {"product": "Feb", "target": 60.0, "region": "a"},
                {"product": "Feb", "target": 25.0, "region": "b"},
            ]});
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            let zero = area_closing_y(&svg, "#ad9c7f");
            let on_b = area_closing_y(&svg, "#779bc9");
            assert!(on_b < zero, "a closes onto b's top, above the zero line: {on_b} vs {zero}");
        }

        /// `_layer_series_color`: a line layer's series color is its stroke only;
        /// an authored point color pins the points, not the line or its legend entry.
        #[test]
        fn a_line_layers_point_color_does_not_color_its_line_or_legend_entry() {
            let mut layer = line_layer_json("#000000");
            layer["ink"] = serde_json::json!("#779bc9");
            layer["line_mark"]["stroke"]["color"] = serde_json::Value::Null;
            layer["point_mark"]["color"] = serde_json::json!("#cc0000");
            let svg = render(&make_payload(payload_with_layer(&[("Jan", 100.0, 40.0)], layer)), &test_fonts()).expect("renders");
            assert!(svg.contains("stroke=\"#779bc9\" stroke-width"), "the line takes the palette slot: {svg}");
            assert!(!svg.contains("stroke=\"#cc0000\" stroke-width=\"2\" stroke-linecap"), "{svg}");
            assert!(svg.contains("d=\"M-10,0L10,0\" fill=\"#779bc9\""), "the legend swatch takes it too: {svg}");
        }

        /// A right-pinned layer's datum is described by the layer: its name and
        /// its own value, not the base measure.
        #[test]
        fn a_pinned_scatter_layers_tooltip_names_the_layer_not_the_base() {
            let mut scatter = scatter_layer_json(false);
            scatter["axis_y"]["position"] = serde_json::json!("right");
            let mut payload_json = payload_with_layer(&[("Jan", 1000.0, 3.1), ("Feb", 1500.0, 4.2)], scatter);
            payload_json["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.1f");
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert!(svg.contains("Target: 3.1\""), "the layer's own value row: {svg}");
            assert!(!svg.contains("revenue: 3.1"), "{svg}");
        }

        /// `_layer_label_slots`: an area layer reads the line label slot alone.
        #[test]
        fn an_area_layers_value_labels_read_the_line_slot_only() {
            let labels = |slot: &str| {
                let mut layer = area_layer_json(None);
                layer[slot]["labels"]["visible"] = serde_json::json!(true);
                area_svg(layer).expect("renders")
            };
            assert!(labels("line_mark").contains(">60</text>"), "the line slot paints");
            assert!(!labels("point_mark").contains(">60</text>"), "the point slot is ignored");
        }

        #[test]
        fn a_pinned_scatter_layer_over_a_color_grouped_base_paints_on_its_own_scale() {
            let mut scatter = scatter_layer_json(false);
            scatter["axis_y"]["position"] = serde_json::json!("right");
            let mut payload_json = make_vertical_payload(&[("Jan", 100.0)], true);
            payload_json["rows"] = serde_json::json!([
                {"product": "Jan", "region": "North", "revenue": 100.0, "target": 3.0},
                {"product": "Jan", "region": "South", "revenue": 60.0, "target": 3.0},
            ]);
            payload_json["resolved"]["layers"] = serde_json::json!([scatter]);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert!(svg.contains(">3</text>"), "the layer's own 0..3 ladder: {svg}");
        }

        #[test]
        fn a_pinned_scatter_layer_over_a_negative_base_domain_draws() {
            let mut scatter = scatter_layer_json(false);
            scatter["axis_y"]["position"] = serde_json::json!("right");
            let mut payload_json = payload_with_layer(&[("Jan", -30.0, 3.0), ("Feb", 45.0, 4.0)], scatter);
            payload_json["resolved"]["style"]["axis_y"]["tick_values"] = serde_json::json!([-40.0, -20.0, 0.0, 20.0, 40.0]);
            payload_json["resolved"]["style"]["axis_y"]["domain_max"] = serde_json::json!(48.6);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert!(svg.contains('\u{2212}'), "the negative rung is labeled: {svg}");
        }

        /// A pinned layer's own axis applies an authored tick count, grid flag and domain.
        #[test]
        fn a_pinned_layer_axis_applies_its_tick_count_grid_and_authored_domain() {
            let layer = |section: &str, key: &str, value: serde_json::Value| {
                let mut scatter = scatter_layer_json(false);
                scatter["axis_y"]["position"] = serde_json::json!("right");
                scatter["axis_y"][section] = serde_json::json!({ key: value });
                render(&make_payload(payload_with_layer(&[("Jan", 1000.0, 3.1)], scatter)), &test_fonts())
            };
            let domained = layer("scale", "domain", serde_json::json!([0, 200])).expect("an authored layer domain renders");
            assert!(domained.contains(">200<"), "the layer ladder tops out at the authored 200: {domained}");
            let ticks = |count: u32| {
                let svg = layer("ticks", "count", serde_json::json!(count)).expect("an authored tick count renders");
                svg.matches("<text").count()
            };
            assert!(ticks(2) < ticks(10), "the layer axis draws the labels its own count asks for");
            let hidden = layer("grid", "visible", serde_json::json!(false)).expect("a hidden layer grid renders");
            let shown = layer("grid", "visible", serde_json::json!(true)).expect("a shown layer grid renders");
            assert!(shown.matches("<line").count() > hidden.matches("<line").count());
        }

        /// A pinned layer value outside the layer's authored domain is cut at the plot
        /// edge while the scale's `clip` holds, and paints past it with `clip: false`.
        #[test]
        fn a_layer_value_past_its_authored_domain_clips_unless_the_scale_unclips() {
            let render_clip = |clip: Option<bool>| {
                let mut scatter = scatter_layer_json(false);
                scatter["axis_y"]["position"] = serde_json::json!("right");
                scatter["axis_y"]["scale"] = serde_json::json!({"domain": [0, 2]});
                if let Some(clip) = clip {
                    scatter["axis_y"]["scale"]["clip"] = serde_json::json!(clip);
                }
                render(&make_payload(payload_with_layer(&[("Jan", 1000.0, 3.1)], scatter)), &test_fonts())
                    .expect("renders")
            };
            assert!(render_clip(None).contains("clip-path=\"url(#dbt-layer-clip"), "the default clips the layer");
            assert!(!render_clip(Some(false)).contains("clip-path"), "clip: false paints past the plot");
        }

        #[test]
        fn an_area_layer_over_a_grouped_bar_stacks_its_rows_per_category() {
            let mut payload_json = make_vertical_payload(&[("Jan", 100.0)], true);
            payload_json["rows"] = serde_json::json!([
                {"product": "Jan", "region": "North", "revenue": 100.0, "target": 90.0},
                {"product": "Jan", "region": "South", "revenue": 60.0, "target": 90.0},
            ]);
            payload_json["resolved"]["layers"] = serde_json::json!([area_layer_json(None)]);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            // Both rows of the category stack (90, then 90 on top of it): the second vertex of
            // the fill's top edge sits above the first.
            let area = &svg[svg.find("mark-area").expect("an area mark")..];
            let d = area.split("d=\"").nth(1).and_then(|r| r.split('"').next()).expect("the area path");
            let ys: Vec<f64> = d.split(['M', 'L']).skip(1).map(|pt| pt.split(',').nth(1).unwrap().trim_end_matches('Z').parse().unwrap()).collect();
            assert!(ys[1] < ys[0], "{ys:?}");
        }

        /// A pinned area paints from its own zero, so its scale always nests a
        /// zero rule, beside the base's.
        #[test]
        fn a_right_pinned_area_layer_nests_its_own_zero_rule() {
            for side in ["right", "left"] {
                let mut layer = area_layer_json(None);
                layer["axis_y"]["position"] = serde_json::json!(side);
                let svg = area_svg(layer).expect("renders");
                assert_eq!(svg.matches("stroke=\"#8A8C8F\" stroke-width=\"2\"").count(), 2, "{side}: {svg}");
            }
        }

        /// A layer's type and its `area_mark` must agree: the painter keys on the mark.
        #[test]
        fn an_area_type_without_an_area_mark_is_rejected_by_name() {
            let mut layer = area_layer_json(None);
            layer.as_object_mut().expect("an object").remove("area_mark");
            let err = area_svg(layer).unwrap_err();
            assert!(err.contains("area_mark"), "{err}");
        }

        /// A layer on the base's own side takes no zero rule of its own; it relies on
        /// the base's (`layer_orients[i] != base_side`).
        #[test]
        fn only_a_layer_off_the_base_side_nests_its_own_zero_rule() {
            let mut pinned = bar_layer_json(None);
            pinned["axis_y"]["position"] = serde_json::json!("right");
            let mut payload_json = payload_with_layer(&[("Jan", 1000.0, 3.1)], pinned.clone());
            payload_json["resolved"]["layers"] = serde_json::json!([pinned, bar_layer_json(None)]);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert_eq!(svg.matches("stroke=\"#8A8C8F\"").count(), 2, "the base's rule and the pinned layer's: {svg}");
        }

        #[test]
        fn several_right_pinned_scatter_layers_each_get_their_own_axis() {
            let mut scatter = scatter_layer_json(false);
            scatter["axis_y"]["position"] = serde_json::json!("right");
            let mut payload_json = payload_with_layer(&[("Jan", 1000.0, 3.1)], scatter.clone());
            payload_json["resolved"]["layers"] = serde_json::json!([scatter.clone(), scatter]);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert_eq!(svg.matches(" data-dct-role=\"y-axis\"").count(), 3, "base and both layers: {svg}");
        }

        /// The y of the plot group's translate: a larger mark ink at the domain top
        /// reserves more room above the plot, so the plot starts lower.
        fn plot_top(svg: &str) -> f64 {
            let open = "<g transform=\"translate(";
            let t = svg.find(open).expect("a plot group") + open.len();
            svg[t..].split([',', ')']).nth(1).expect("y").parse().expect("number")
        }

        fn pinned_scatter_plot_top(size: f64, filled: bool, stroke_width: f64) -> f64 {
            let mut layer = scatter_layer_json(false);
            layer["axis_y"]["position"] = serde_json::json!("right");
            layer["point_mark"]["size"] = serde_json::json!(size);
            layer["point_mark"]["filled"] = serde_json::json!(filled);
            layer["point_mark"]["stroke_width"] = serde_json::json!(stroke_width);
            let mut payload_json = payload_with_layer(&[("Jan", 1000.0, 4.0), ("Feb", 1500.0, 4.5)], layer);
            payload_json["resolved"]["legend"]["visible"] = serde_json::json!(false);
            plot_top(&render(&make_payload(payload_json), &test_fonts()).expect("renders"))
        }

        #[test]
        fn a_pinned_scatter_point_at_the_domain_top_reserves_its_radius_and_a_hollow_ring_its_stroke() {
            assert!(pinned_scatter_plot_top(900.0, true, 1.0) > pinned_scatter_plot_top(100.0, true, 1.0));
            assert!(pinned_scatter_plot_top(100.0, false, 6.0) > pinned_scatter_plot_top(100.0, false, 1.0));
            assert!(pinned_scatter_plot_top(100.0, false, 6.0) > pinned_scatter_plot_top(100.0, true, 6.0));
        }

        /// A pinned line layer's stroke at the domain top reaches past the plot.
        #[test]
        fn a_pinned_line_layer_at_the_domain_top_reserves_its_stroke() {
            let top = |width: f64| {
                let mut layer = line_layer_json("#dea628");
                layer["axis_y"]["position"] = serde_json::json!("right");
                layer["line_mark"]["stroke"]["width"] = serde_json::json!(width);
                let mut payload_json = payload_with_layer(&[("Jan", 1000.0, 4.0), ("Feb", 1500.0, 4.5)], layer);
                payload_json["resolved"]["legend"]["visible"] = serde_json::json!(false);
                plot_top(&render(&make_payload(payload_json), &test_fonts()).expect("renders"))
            };
            assert!(top(12.0) > top(1.0), "{} vs {}", top(12.0), top(1.0));
        }

        /// A color-grouped line layer's paths are scope groups, so the stroke-expanded
        /// top of a path on the plot's top edge joins `yBounds` and pushes a top legend
        /// up by it; an ungrouped layer's bare mark reaches the view bounds only.
        #[test]
        fn a_color_grouped_line_layer_at_the_plot_top_pushes_a_top_legend_up_by_its_stroke() {
            let top = |grouped: bool| {
                let mut layer = line_layer_json("#dea628");
                layer["axis_y"]["position"] = serde_json::json!("right");
                layer["line_mark"]["stroke"]["width"] = serde_json::json!(3.0);
                if grouped {
                    layer["color"] = serde_json::json!("product");
                    layer["ink"] = serde_json::Value::Null;
                    layer["series_inks"] = serde_json::json!({"Jan": "#779bc9", "Feb": "#ad9c7f"});
                }
                let mut payload_json = payload_with_layer(&[("Jan", 1000.0, 4.0), ("Feb", 1500.0, 5.0)], layer);
                payload_json["resolved"]["legend"]["title"]["visible"] = serde_json::json!(false);
                plot_top(&render(&make_payload(payload_json), &test_fonts()).expect("renders"))
            };
            assert_eq!(top(true) - top(false), 3.0, "{} vs {}", top(true), top(false));
        }

        /// A pinned layer reaching zero nests its own zero rule, beside the base's
        /// own; with the rule off neither draws.
        #[test]
        fn a_pinned_layer_with_a_value_at_zero_nests_its_own_zero_rule() {
            let mut scatter = scatter_layer_json(false);
            scatter["axis_y"]["position"] = serde_json::json!("right");
            let rules = |visible: bool| {
                let mut payload_json = payload_with_layer(&[("Jan", 1000.0, 0.0), ("Feb", 1500.0, 4.0)], scatter.clone());
                payload_json["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(visible);
                payload_json["resolved"]["style"]["axis_y"]["grid"]["threshold"] =
                    serde_json::json!({"color": "#8A8C8F", "width": 2.0});
                let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
                svg.matches("stroke=\"#8A8C8F\" stroke-width=\"2\"").count()
            };
            assert_eq!(rules(true), 2);
            assert_eq!(rules(false), 0);
        }

        /// A pinned line layer's drawn points are unclipped, so a big point at the
        /// domain top reserves more room than a small one.
        #[test]
        fn a_pinned_line_layers_drawn_points_join_the_fit() {
            let top = |size: f64| {
                let mut layer = line_layer_json("#dea628");
                layer["axis_y"]["position"] = serde_json::json!("right");
                layer["line_mark"]["stroke"]["width"] = serde_json::json!(1.0);
                layer["point_mark"]["size"] = serde_json::json!(size);
                let mut payload_json = payload_with_layer(&[("Jan", 1000.0, 4.0), ("Feb", 1500.0, 4.5)], layer);
                payload_json["resolved"]["legend"]["visible"] = serde_json::json!(false);
                plot_top(&render(&make_payload(payload_json), &test_fonts()).expect("renders"))
            };
            assert!(top(900.0) > top(48.0), "{} vs {}", top(900.0), top(48.0));
        }

        /// A pinned layer's scale is its own zero union with its own values, so a
        /// negative value stretches the domain below zero: the layer's ladder
        /// carries a negative rung and its zero rule sits above the plot floor.
        #[test]
        fn a_negative_value_on_a_pinned_layer_extends_its_scale_below_zero() {
            let mut scatter = scatter_layer_json(false);
            scatter["axis_y"]["position"] = serde_json::json!("right");
            let mut line = line_layer_json("#dea628");
            line["axis_y"]["position"] = serde_json::json!("right");
            for layer in [scatter, line] {
                let svg = render(&make_payload(payload_with_layer(&[("Jan", 1000.0, -2.0), ("Feb", 1500.0, 4.0)], layer)), &test_fonts())
                    .expect("renders");
                assert!(svg.contains(">\u{2212}2</text>") || svg.contains(">-2</text>"), "a negative rung: {svg}");
                let zero_rules = svg.matches("stroke=\"#8A8C8F\" stroke-width=\"2\"").count();
                assert_eq!(zero_rules, 2, "the base's rule and the layer's own: {svg}");
            }
        }

        #[test]
        fn a_right_pinned_scatter_layer_paints_on_its_own_scale() {
            let mut layer = scatter_layer_json(false);
            layer["axis_y"]["position"] = serde_json::json!("right");
            let own = render(&make_payload(payload_with_layer(&[("Jan", 1000.0, 3.1), ("Feb", 1500.0, 4.2)], layer)), &test_fonts())
                .expect("renders");
            let shared = render(
                &make_payload(payload_with_layer(&[("Jan", 1000.0, 3.1), ("Feb", 1500.0, 4.2)], scatter_layer_json(false))),
                &test_fonts(),
            )
            .expect("renders");
            // On the base's 0..1600 scale a 3.1 point sits at the plot floor, on its own 0..4.5 scale mid-plot.
            let cy = |svg: &str| -> f64 {
                let at = svg.find("<circle").expect("a scatter point");
                let rest = &svg[at..];
                let t = rest.find("translate(").expect("a placed point") + "translate(".len();
                rest[t..].split([',', ')']).nth(1).expect("y").parse().expect("number")
            };
            assert!(cy(&own) < cy(&shared) - 50.0, "{} vs {}", cy(&own), cy(&shared));
        }

        fn stacked_layer_payload(layer: serde_json::Value) -> serde_json::Value {
            let mut payload_json = payload_with_layer(&[("Jan", 100.0, 90.0)], layer);
            payload_json["rows"] = serde_json::json!([
                {"product": "Jan", "segment": "A", "revenue": 100.0, "target": 90.0},
                {"product": "Jan", "segment": "B", "revenue": 40.0, "target": 90.0},
            ]);
            payload_json["resolved"]["color"] = serde_json::json!("segment");
            payload_json["resolved"]["stack"] = serde_json::json!("zero");
            payload_json
        }

        #[test]
        fn a_pinned_line_layer_over_a_stacked_base_renders_on_either_side() {
            for side in ["left", "right"] {
                let mut layer = line_layer_json("#dea628");
                layer["axis_y"]["position"] = serde_json::json!(side);
                let mut payload_json = stacked_layer_payload(layer);
                payload_json["resolved"]["stacked_domain_max"] = serde_json::json!(200.0);
                render(&make_payload(payload_json), &test_fonts()).unwrap_or_else(|e| panic!("{side}: {e}"));
            }
        }

        /// A filled-point line layer grouped by `region` over a one-category base.
        fn grouped_layer_svg(edit: impl FnOnce(&mut serde_json::Value)) -> String {
            let mut layer = line_layer_json("#cc0000");
            layer["color"] = serde_json::json!("region");
            layer["query_name"] = serde_json::json!("regional");
            layer["point_mark"]["filled"] = serde_json::json!(true);
            layer["ink"] = serde_json::Value::Null;
            layer["series_inks"] = serde_json::json!({"East": "#779bc9", "West": "#ad9c7f"});
            edit(&mut layer);
            let mut payload_json = payload_with_layer(&[("Jan", 100.0, 90.0)], layer);
            payload_json["resolved"]["query_name"] = serde_json::json!("base");
            payload_json["datasets"] = serde_json::json!({"regional": [
                {"product": "Jan", "target": 50.0, "region": "East"},
                {"product": "Jan", "target": 60.0, "region": "West"},
            ]});
            render(&make_payload(payload_json), &test_fonts()).expect("renders")
        }

        /// An authored stroke pins the lines of a color-grouped layer; its points
        /// still follow their series.
        #[test]
        fn an_authored_stroke_pins_the_lines_of_a_color_grouped_layer_and_not_its_points() {
            let svg = grouped_layer_svg(|layer| layer["point_mark"]["color"] = serde_json::Value::Null);
            assert_eq!(svg.matches("stroke=\"#cc0000\" stroke-width").count(), 2, "{svg}");
            assert!(!svg.contains("fill=\"#cc0000\""), "points keep their series colors: {svg}");
            assert!(svg.contains("<circle transform=\"translate(") && svg.contains("fill=\"#779bc9\""), "{svg}");
        }

        /// An authored point color pins the points of a color-grouped layer; its
        /// lines still follow their series.
        #[test]
        fn an_authored_point_color_pins_the_points_of_a_color_grouped_layer_and_not_its_lines() {
            let svg = grouped_layer_svg(|layer| layer["line_mark"]["stroke"]["color"] = serde_json::Value::Null);
            assert!(!svg.contains("stroke=\"#cc0000\" stroke-width"), "lines keep their series colors: {svg}");
            assert_eq!(svg.matches("fill=\"#cc0000\"").count(), 2, "{svg}");
            assert!(svg.contains("stroke=\"#779bc9\" stroke-width"), "{svg}");
        }

        /// A dual-axis layer's own category joins the shared band domain.
        #[test]
        fn a_dual_axis_layer_row_at_a_new_category_widens_the_band_domain() {
            let mut layer = dual_axis_layer_json();
            layer["query_name"] = serde_json::json!("targets");
            let mut payload_json = payload_with_layer(&[("Jan", 1000.0, 3.1)], layer);
            payload_json["resolved"]["query_name"] = serde_json::json!("base");
            payload_json["datasets"] = serde_json::json!({"targets": [
                {"product": "Jan", "target": 30.0},
                {"product": "Feb", "target": 40.0},
            ]});
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert!(svg.find(">Jan<").unwrap() < svg.find(">Feb<").unwrap(), "{svg}");
        }

        /// A pinned domain is verbatim without a layer and nice-rounded once a
        /// shared-scale layer joins it.
        #[test]
        fn an_authored_y_domain_beside_a_layer_is_nice_rounded() {
            let mut payload_json =
                payload_with_layer(&[("Jan", 100.0, 90.0), ("Feb", 150.0, 140.0)], line_layer_json("#dea628"));
            payload_json["resolved"]["style"]["axis_y"]["scale"] =
                serde_json::json!({"continuous": {"domain": [0.0, 1950.0]}});
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert!(svg.contains(">2,000</text>"), "{svg}");
        }

        #[test]
        fn a_bar_layer_beside_an_authored_sort_renders() {
            let layer = bar_layer_json(Some("projected"));
            let mut payload_json = make_vertical_payload(&[("1", 120.0), ("3", 150.0)], false);
            payload_json["resolved"]["query_name"] = serde_json::json!("actual");
            payload_json["resolved"]["sort"] = serde_json::json!({"by": "revenue", "order": "desc"});
            payload_json["resolved"]["layers"] = serde_json::json!([layer]);
            payload_json["resolved"]["legend"]["visible"] = serde_json::json!(true);
            payload_json["datasets"] = serde_json::json!({"projected": [{"product": "2", "target": 140.0}]});
            render(&make_payload(payload_json), &test_fonts()).expect("renders");
        }

        fn horizontal_layer_payload(layer: serde_json::Value) -> serde_json::Value {
            let mut payload_json = make_horizontal_payload();
            payload_json["rows"] = serde_json::json!([
                {"product": "Jan", "revenue": 100.0, "target": 90.0},
                {"product": "Feb", "revenue": 150.0, "target": 140.0},
            ]);
            payload_json["resolved"]["layers"] = serde_json::json!([layer]);
            payload_json["resolved"]["legend"]["visible"] = serde_json::json!(true);
            payload_json
        }

        #[test]
        fn a_horizontal_bar_line_layer_paints_on_the_shared_scale() {
            let svg = render(&make_payload(horizontal_layer_payload(line_layer_json("#dea628"))), &test_fonts())
                .expect("renders");
            assert!(svg.contains("stroke=\"#dea628\""), "{svg}");
        }

        /// A layer row at a category the base lacks widens the band domain, but the
        /// label gutter stays what the base's own categories bake.
        #[test]
        fn a_horizontal_bar_layer_row_at_a_new_category_widens_the_bands_not_the_label_gutter() {
            let render_with = |extra: &[&str]| {
                let mut layer = line_layer_json("#dea628");
                layer["query_name"] = serde_json::json!("extra");
                let mut payload_json = horizontal_layer_payload(layer);
                payload_json["resolved"]["query_name"] = serde_json::json!("base");
                let mut rows = vec![serde_json::json!({"product": "Jan", "target": 30.0})];
                rows.extend(extra.iter().map(|product| serde_json::json!({"product": product, "target": 40.0})));
                payload_json["datasets"] = serde_json::json!({ "extra": rows });
                render(&make_payload(payload_json), &test_fonts()).expect("renders")
            };
            let narrow = render_with(&[]);
            let widened = render_with(&["Marketing"]);
            assert!(widened.contains(">Marketing<"), "{widened}");
            assert_eq!(plot_group_left(&widened), plot_group_left(&narrow), "the gutter is the base's");
        }

        /// A layer's date columns become day numbers; a base end it reuses stays numeric; any
        /// other numeric column, or a non-date text, refuses; a pinned layer is left alone.
        #[test]
        fn layer_dates_become_day_numbers_and_anything_else_refuses() {
            let payload = |mid: serde_json::Value| {
                let mut json = make_vertical_payload(&[("Jan", 100.0)], false);
                json["rows"][0]["mid"] = mid;
                json["rows"][0]["end"] = serde_json::json!(20476);
                make_payload(json)
            };
            let convert = |mid: serde_json::Value, layers: &[serde_json::Value]| {
                crate::families::span::with_layer_day_numbers(&payload(mid), None, ["end", "start"], layers)
            };
            let layers = [serde_json::json!({"y": "mid"}), serde_json::json!({"y": "end"})];
            let converted = convert(serde_json::json!("2026-01-12"), &layers).unwrap();
            assert_eq!(converted.rows[0]["mid"], serde_json::json!(20465.0));
            assert_eq!(converted.rows[0]["end"].as_f64(), Some(20476.0));
            let twice = [serde_json::json!({"y": "mid"}), serde_json::json!({"y": "mid"})];
            assert!(convert(serde_json::json!("2026-01-12"), &twice).is_ok(), "two layers may read one column");
            let Err(err) = convert(serde_json::json!("soon"), &layers) else { panic!("a non-date text must refuse") };
            assert!(err.contains("is not a date"), "{err}");
            let Err(err) = convert(serde_json::json!(5), &layers) else { panic!("a number must refuse") };
            assert!(err.contains("not a date"), "{err}");
            let pinned = [serde_json::json!({"y": "mid", "axis_y": {"position": "right"}})];
            let kept = convert(serde_json::json!(5), &pinned).unwrap();
            assert_eq!(kept.rows[0]["mid"], serde_json::json!(5));
        }

        /// The calendar axis takes every unpinned layer's y and y_start, and no pinned layer's.
        #[test]
        fn the_date_axis_collects_the_unpinned_layers_ends() {
            let rows = vec![serde_json::json!({"mid": 5.0, "lo": 1.0, "hi": 9.0}).as_object().unwrap().clone()];
            let mut ranged = bar_layer_json(None);
            ranged["y"] = serde_json::json!("hi");
            ranged["y_start"] = serde_json::json!("lo");
            let mut pinned = line_layer_json("#dea628");
            pinned["y"] = serde_json::json!("mid");
            pinned["axis_y"]["position"] = serde_json::json!("right");
            let layers = [
                OverlayLayer::Bar(Box::new(OverlayBarLayer::parse(&ranged, "bar").unwrap())),
                OverlayLayer::Line(Box::new(OverlayLineLayer::parse(&pinned, "bar").unwrap())),
            ];
            let days = layer_day_numbers(layers.iter().map(|l| (l, rows.as_slice())));
            assert_eq!(days, vec![9.0, 1.0]);
        }

        /// A horizontal-bar line layer paints its authored axis side, curve, labels and color.
        #[test]
        fn a_horizontal_bar_line_layer_paints_what_it_is_authored_with() {
            for patch in [
                ("axis_y", "position", serde_json::json!("right")),
                ("line_mark", "curve", serde_json::json!("step")),
                ("line_mark", "labels", serde_json::json!({"visible": true})),
            ] {
                let mut layer = line_layer_json("#dea628");
                layer[patch.0][patch.1] = patch.2.clone();
                render(&make_payload(horizontal_layer_payload(layer)), &test_fonts()).unwrap_or_else(|err| panic!("{patch:?}: {err}"));
            }
            let mut layer = line_layer_json("#dea628");
            layer["color"] = serde_json::json!("region");
            render(&make_payload(horizontal_layer_payload(layer)), &test_fonts()).expect("renders");
        }

        fn scatter_layer_json(labels_visible: bool) -> serde_json::Value {
            serde_json::json!({
                "type": "scatter",
                "point_mark": {
                    "size": 100.0, "color": "#dea628", "filled": true, "stroke_width": 2.0,
                    "labels": {"visible": labels_visible}
                },
                "axis_y": {"position": null},
                "x": null,
                "y": "target",
                "label": null,
                "color": null,
                "ink": "#dea628", "query_name": null
            })
        }

        /// A scatter layer grouped by its own color paints each point in its value's
        /// claimed color, outranking an authored point color, and draws the values as
        /// circles in the legend beside the base's square.
        #[test]
        fn a_scatter_layer_with_its_own_color_paints_each_point_in_its_values_color() {
            let mut scatter = scatter_layer_json(false);
            scatter["color"] = serde_json::json!("region");
            scatter["ink"] = serde_json::Value::Null;
            scatter["series_inks"] = serde_json::json!({"East": "#779bc9", "West": "#ad9c7f"});
            scatter["query_name"] = serde_json::json!("regional");
            let mut payload_json = payload_with_layer(&[("Jan", 100.0, 40.0), ("Feb", 120.0, 60.0)], scatter);
            payload_json["resolved"]["query_name"] = serde_json::json!("base");
            payload_json["datasets"] = serde_json::json!({"regional": [
                {"product": "Jan", "target": 40.0, "region": "East"},
                {"product": "Jan", "target": 20.0, "region": "West"},
                {"product": "Feb", "target": 60.0, "region": "East"},
            ]});
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert!(!svg.contains("#dea628"), "the encoding outranks the authored point color: {svg}");
            assert_eq!(svg.matches("r=\"5.000\" fill=\"#779bc9\"").count(), 2, "East takes palette[1]: {svg}");
            assert_eq!(svg.matches("r=\"5.000\" fill=\"#ad9c7f\"").count(), 1, "West takes palette[2]: {svg}");
            let ring = "A5,5,0,1,1,5,0\" fill=\"#779bc9\" stroke=\"none\"";
            assert!(svg.contains(ring), "East's legend circle: {svg}");
        }

        /// A legend beside the plot is titled by the color field of a layer grouping on
        /// its own, as Vega-Lite names a merged legend after its implicit field titles.
        #[test]
        fn a_side_legend_is_titled_by_the_color_field_of_a_layer_over_a_colorless_base() {
            let mut scatter = scatter_layer_json(false);
            scatter["color"] = serde_json::json!("region");
            scatter["ink"] = serde_json::Value::Null;
            scatter["series_inks"] = serde_json::json!({"East": "#779bc9", "West": "#ad9c7f"});
            scatter["query_name"] = serde_json::json!("regional");
            let mut payload_json = payload_with_layer(&[("Jan", 100.0, 40.0)], scatter);
            payload_json["resolved"]["query_name"] = serde_json::json!("base");
            payload_json["resolved"]["legend"]["position"] =
                serde_json::json!({"edge": "right", "align": "start", "overlay": false});
            payload_json["resolved"]["legend"]["direction"] = serde_json::json!("vertical");
            payload_json["datasets"] = serde_json::json!({"regional": [
                {"product": "Jan", "target": 40.0, "region": "East"},
                {"product": "Jan", "target": 20.0, "region": "West"},
            ]});
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert_eq!(svg.matches(">region</text>").count(), 1, "the layer's field names the legend: {svg}");
        }

        #[test]
        fn a_scatter_layer_with_its_own_color_over_a_ranged_bar_leads_its_block_with_a_swatched_series() {
            let mut scatter = scatter_layer_json(false);
            scatter["color"] = serde_json::json!("product");
            scatter["series_inks"] = serde_json::json!({"Alpha": "#779bc9", "Beta": "#ad9c7f"});
            let mut payload_json = ranged_payload(false);
            for row in payload_json["rows"].as_array_mut().unwrap() {
                row["target"] = serde_json::json!(500.0);
            }
            payload_json["resolved"]["layers"] = serde_json::json!([scatter]);
            payload_json["resolved"]["legend"]["visible"] = serde_json::json!(true);
            payload_json["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.0f");
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            let swatch = crate::contract::SWATCH;
            assert!(svg.contains(&format!("{swatch}#779bc9{swatch}Alpha; ")), "Alpha's own paint leads its block: {svg}");
            assert!(svg.contains(&format!("{swatch}#ad9c7f{swatch}Beta; ")), "Beta's own paint leads its block: {svg}");
            assert_eq!(svg.matches(swatch).count(), 8, "two brackets on each of the bar's and the point's two labels: {svg}");
        }

        #[test]
        fn a_scatter_layer_prints_its_values_over_a_plain_vertical_bar() {
            let payload = make_payload(payload_with_layer(
                &[("Jan", 100.0, 41.5), ("Feb", 120.0, 63.5)],
                scatter_layer_json(true),
            ));
            let svg = render(&payload, &test_fonts()).expect("renders");
            assert!(svg.contains(">41.5</text>") && svg.contains(">63.5</text>"), "{svg}");
        }

        /// A layer label past the plot top moves the fit margins instead of refusing.
        #[test]
        fn a_line_layer_label_past_the_plot_top_joins_the_fit() {
            // The grid's bottom rule sits at the plot height: reserved label ink above shrinks it.
            let plot_h = |labels: bool| {
                let mut layer = line_layer_json("#dea628");
                layer["line_mark"]["labels"]["visible"] = serde_json::json!(labels);
                let mut payload_json = payload_with_layer(&[("Jan", 90.0, 2000.0), ("Feb", 100.0, 2000.0)], layer);
                payload_json["resolved"]["legend"]["visible"] = serde_json::json!(false);
                let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
                svg.split("<line x1=\"0.5\" y1=\"")
                    .skip(1)
                    .map(|r| r.split('"').next().expect("y").parse::<f64>().expect("number"))
                    .fold(0.0_f64, f64::max)
            };
            assert!(plot_h(true) < plot_h(false), "{} vs {}", plot_h(true), plot_h(false));
        }

        #[test]
        fn a_scatter_layer_prints_no_labels_unless_asked() {
            let payload = make_payload(payload_with_layer(
                &[("Jan", 100.0, 41.5), ("Feb", 120.0, 63.5)],
                scatter_layer_json(false),
            ));
            let svg = render(&payload, &test_fonts()).expect("renders");
            assert!(!svg.contains(">41.5</text>"), "{svg}");
        }

        fn dual_axis_layer_json() -> serde_json::Value {
            let mut layer = line_layer_json("#dea628");
            layer["axis_y"]["position"] = serde_json::json!("right");
            layer["axis_y"]["title"] = serde_json::json!("Conversion (%)");
            layer
        }

        /// A visible tick lands on both value axes of a dual-axis bar (the
        /// layer's axis is built from the base's own tick style): the left
        /// axis gives up the tick length like any left axis, the layer's
        /// right axis gives up the same again, and every label keeps its
        /// absolute x.
        #[test]
        fn dual_axis_paints_both_value_ticks_and_reserves_their_length() {
            let render_with = |visible: bool, length: Option<f64>| {
                let mut payload_json = payload_with_layer(
                    &[("Jan", 1000.0, 3.1), ("Feb", 1500.0, 3.4), ("Mar", 1200.0, 4.2)],
                    dual_axis_layer_json(),
                );
                let axis_y = &mut payload_json["resolved"]["style"]["axis_y"];
                axis_y["ticks"]["visible"] = serde_json::json!(visible);
                if let Some(len) = length {
                    axis_y["ticks"]["length"] = serde_json::json!(len);
                }
                render(&make_payload(payload_json), &test_fonts()).expect("dual-axis bar renders")
            };
            let plot_w = |svg: &str| {
                let (x1, x2, _) = *horizontal_lines(svg).first().expect("a gridline");
                x2 - x1
            };
            let off = render_with(false, None);
            for (length, expected) in [(None, 6.0), (Some(14.0), 14.0)] {
                let on = render_with(true, length);
                let left = plot_group_left(&on);
                let right = left + plot_w(&on);
                let ticks = tick_marks(&on, expected);
                let (base, layer): (Vec<_>, Vec<_>) =
                    ticks.iter().partition(|(_, x2, _)| *x2 < left + 1.0);
                assert_eq!(base.len(), 5, "the base ladder 0..2000 ({length:?}): {on}");
                assert_eq!(layer.len(), 5, "the layer ladder 0..4 ({length:?}): {on}");
                assert!(base.iter().all(|(_, x2, _)| *x2 == left + 0.5), "{length:?}");
                assert!(layer.iter().all(|(x1, _, _)| *x1 == right + 0.5), "{length:?}");
                assert_eq!(plot_group_left(&on) - plot_group_left(&off), expected, "{length:?}");
                assert_eq!(plot_w(&off) - plot_w(&on), 2.0 * expected, "{length:?}");
                assert_eq!(value_label_xs(&on), value_label_xs(&off), "{length:?}");
            }
            assert!(tick_marks(&off, 6.0).is_empty());
        }

        /// A dual-axis layer reading a different query scales its own axis from
        /// that query's rows, never the base's.
        #[test]
        fn a_dual_axis_layer_reading_a_different_query_scales_from_its_own_rows() {
            let mut layer = dual_axis_layer_json();
            layer["query_name"] = serde_json::json!("targets_query");
            let mut payload_json =
                payload_with_layer(&[("Jan", 1000.0, 3.1), ("Feb", 1500.0, 3.4)], layer);
            payload_json["resolved"]["query_name"] = serde_json::json!("base_query");
            payload_json["datasets"] = serde_json::json!({
                "targets_query": [
                    {"product": "Jan", "target": 30.0},
                    {"product": "Feb", "target": 40.0},
                ]
            });
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert!(svg.contains(">40</text>"), "the layer axis tops out at its own max: {svg}");
        }

        /// A dual-axis layer over a grouped base beside a right-hand legend: the
        /// legend clears the layer's own axis, not just the plot.
        #[test]
        fn a_dual_axis_layer_over_a_grouped_base_leaves_room_for_a_right_hand_legend() {
            let mut payload_json = make_vertical_payload(&[("Jan", 100.0), ("Feb", 150.0)], true);
            payload_json["rows"] = serde_json::json!([
                {"product": "Jan", "region": "North", "revenue": 100.0, "target": 90.0},
                {"product": "Jan", "region": "South", "revenue": 60.0, "target": 90.0},
                {"product": "Feb", "region": "North", "revenue": 150.0, "target": 140.0},
                {"product": "Feb", "region": "South", "revenue": 90.0, "target": 140.0},
            ]);
            payload_json["resolved"]["layers"] = serde_json::json!([dual_axis_layer_json()]);
            payload_json["resolved"]["legend"]["visible"] = serde_json::json!(true);
            payload_json["resolved"]["legend"]["position"]["edge"] = serde_json::json!("right");
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            let x_of = |marker: &str| -> f64 {
                let at = svg.find(marker).unwrap_or_else(|| panic!("{marker}: {svg}")) + marker.len();
                svg[at..].split([' ', '"']).next().expect("x").parse().expect("number")
            };
            let layer_title_x = x_of("rotate(90 ");
            let legend_x = x_of("<rect x=\"");
            assert!(legend_x > layer_title_x + 5.0, "legend at {legend_x} clears the layer axis title at {layer_title_x}: {svg}");
        }

        /// A `right`-pinned layer takes its own independent right-orient
        /// scale: the base re-pins to the LEFT (`_resolved_layer_y_
        /// orients`, `_overlay.py:633`), both axes draw their own grid
        /// (probed: `layered-charts_0`'s reference carries two separate
        /// `role-axis-grid` groups), and paint order is bar → zero rule →
        /// layer (`_overlay.py`'s dual-axis zero-rule nesting — the
        /// OPPOSITE of the shared-scale order).
        /// `_overlay.py`'s dual-axis re-pin pops the base's own
        /// `labelAlign`/`labelPadding` when the orient actually flips,
        /// leaving Vega's compiled default (`2.0`, probed via
        /// `render_chart_artifact` on `dual-y-axis-lab/revenue_vs_
        /// conversion`'s own re-pinned base axis) rather than the theme's
        /// `axis_y.labels.padding` (`6.0`). Probed: removing the fix moves
        /// the base's own gutter (and every left-side label) 4px wider
        /// than production's reference (`plot translate(71,74)` reference
        /// vs `(75.00,75.00)` pre-fix).
        #[test]
        fn a_re_pinned_base_axis_uses_vegas_default_label_padding_not_the_theme() {
            let mut payload_json = payload_with_layer(
                &[("Jan", 1000.0, 3.1), ("Feb", 1500.0, 3.4), ("Mar", 1200.0, 4.2)],
                dual_axis_layer_json(),
            );
            payload_json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("right");
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("dual-axis bar renders");
            let plot_left: f64 = svg
                .split("<g transform=\"translate(")
                .nth(1)
                .and_then(|s| s.split(',').next())
                .expect("plot group carries a translate")
                .parse()
                .expect("plot_left is numeric");
            // The base's own left-side label (e.g. "500") anchors at
            // `plot_left - ax_off_y + 0.5 - base_label_pad`. `ax_off_y` is
            // 0 for this theme (ticks invisible/unoffset), so with the fix
            // (`base_label_pad == 2.0`) the label sits at `plot_left - 1.5`
            // — 4px closer to the axis than the theme's own padding (6.0)
            // would place it (`plot_left - 5.5`).
            let label_x: f64 = svg
                .split(">500<")
                .next()
                .and_then(|s| s.rsplit("x=\"").next())
                .and_then(|s| s.split('"').next())
                .expect("base tick label carries an x attr")
                .parse()
                .expect("label x is numeric");
            assert!(
                (label_x - (plot_left - 1.5)).abs() < 0.01,
                "label_x {label_x} should sit at plot_left({plot_left}) - 1.5 \
                 (base_label_pad 2.0), not plot_left - 5.5 (the theme's 6.0): {svg}"
            );
        }

        /// `_overlay.py`'s dual-axis layer template builds `layer_ay_vl`
        /// from the bare `ay_vl` (`axis_to_vl(axis_y)`), dropping only
        /// `labelAlign` (`:1447-1449`) — UNLIKE the shared-scale branch
        /// (`:1465-1469`), it never routes through `measure_axis_to_vl`'s
        /// own-side-invasion safety net (the real-measured-width `+4`
        /// breathing-room hack `VALUE_AXIS_BREATHING_ROOM` exists for).
        /// That hack is only correct when `labelAlign` gets FORCED toward
        /// the plot; the layer's own axis never gets that force (no
        /// `labelAlign` key at all — Vega-Lite's own default for
        /// `orient: right` grows labels AWAY from the plot, which
        /// `autosize: fit` already reserves for correctly on its own), so
        /// the layer's gutter should be the SAME "real font + themed
        /// labelPadding" law `base_label_pad`'s un-re-pinned branch already
        /// uses, not the base's OWN val-on-right hack. Probed (`gs.
        /// _prod_render`, `RenderArtifact` payload patched directly, full
        /// `render_chart_artifact` pipeline): removing the layer's own axis
        /// title moves `dual-y-axis-lab/revenue_vs_conversion`'s plot width
        /// 478 → 499 (Δ21, matches `title_extra`'s own formula exactly —
        /// untouched); hiding the layer's own labels entirely moves it
        /// 478 → 492 (Δ14 — the layer's ENTIRE gutter, label width plus
        /// padding); patching `labelPadding` from its compiled `6.0` to
        /// `0.0` moves it 478 → 484 (Δ6, the SAME `6.0` 1:1 the padding
        /// module's own docstring documents) — so production's real gutter
        /// is `width(8) + labelPadding(6) = 14`, not the port's own
        /// `width(8, Inter stand-in) + VALUE_AXIS_BREATHING_ROOM(4) = 12`.
        #[test]
        fn a_layer_axis_uses_the_themed_label_padding_not_the_base_s_val_on_right_hack() {
            let _locale = crate::time_locale::en_us_scope();
            let mut payload_json = payload_with_layer(
                &[("Jan", 1000.0, 3.1), ("Feb", 1500.0, 3.4), ("Mar", 1200.0, 4.2)],
                dual_axis_layer_json(),
            );
            payload_json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("right");
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("dual-axis bar renders");

            let plot_left: f64 = svg
                .split("<g transform=\"translate(")
                .nth(1)
                .and_then(|s| s.split(',').next())
                .expect("plot group carries a translate")
                .parse()
                .expect("plot_left is numeric");
            let grid_x2: f64 = svg
                .split("<line x1=\"0.5\" y1=\"")
                .nth(1)
                .and_then(|s| s.split("x2=\"").nth(1))
                .and_then(|s| s.split('"').next())
                .expect("first grid rule carries an x2 attr")
                .parse()
                .expect("grid x2 is numeric");
            let plot_right = plot_left + (grid_x2 - 0.5);

            // The layer's own ladder (`d3_nice(0, 4.2, 10) == 4.5`,
            // `d3_ticks(0, 4.5, 6) == [0, 1, 2, 3, 4]`, all single digits —
            // the widest, "4", is `fonts.measure("4", TEST_FONT, 11.0)`. The
            // labels start-anchor at the padding; the gutter's far edge is
            // where the rotated title's own `titlePadding` starts.
            let widest = test_fonts().measure("4", TEST_FONT, 11.0).expect("test font measures \"4\"");
            let expected_x = plot_right + 0.5 + widest + 6.0;
            let buggy_x = plot_right + 0.5 + widest + VALUE_AXIS_BREATHING_ROOM;

            let title_i = svg.find(">Conversion (%)</text>").expect("layer title draws");
            let title_tag = &svg[svg[..title_i].rfind("<text").unwrap()..title_i];
            let title_x: f64 = title_tag
                .split("x=\"")
                .nth(1)
                .and_then(|s| s.split('"').next())
                .expect("layer title carries an x attr")
                .parse()
                .expect("title x is numeric");
            let label_x: f64 = svg
                .split(">4<")
                .next()
                .and_then(|s| s.rsplit("x=\"").next())
                .and_then(|s| s.split('"').next())
                .expect("layer tick label \"4\" carries an x attr")
                .parse()
                .expect("label x is numeric");
            assert!(
                (label_x - (plot_right + 0.5 + 6.0)).abs() < 0.01,
                "layer label \"4\" at x={label_x} should start at plot_right({plot_right}) + themed labelPadding(6.0): {svg}"
            );
            // The fixture hides the theme's axis title, so the layer's title
            // takes Vega's own default style (`vega_default_axis_title`) and
            // `emit_axis_title_rotated` places it that padding plus the
            // rotated nudge past the gutter's far edge.
            let title_pad = crate::common::vega_default_axis_title(None).padding.expect("vega default padding");
            let title_offset = title_pad + crate::common::rotated_axis_title_nudge(11.0);
            assert!(
                (title_x - (expected_x + title_offset)).abs() < 0.01,
                "layer title at x={title_x} should sit {title_offset} past plot_right({plot_right}) + \
                 real_width({widest:.3}) + themed labelPadding(6.0) = {expected_x:.3}, \
                 not + VALUE_AXIS_BREATHING_ROOM(4.0) = {buggy_x:.3}: {svg}"
            );
        }

        #[test]
        fn a_right_pinned_layer_gets_its_own_independent_right_axis() {
            let mut payload_json = payload_with_layer(
                &[("Jan", 1000.0, 3.1), ("Feb", 1500.0, 3.4), ("Mar", 1200.0, 4.2)],
                dual_axis_layer_json(),
            );
            payload_json["resolved"]["style"]["axis_y"]["title"]["visible"] = serde_json::json!(true);
            // The vertical bar family's own unlayered default is "right"
            // (`_bake_ay_orient`'s "the family default (right)" comment,
            // probed on every dual-axis corpus row) — author it here too,
            // so the base actually needs the re-pin this test checks for
            // rather than starting on the left already.
            payload_json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("right");
            let payload = make_payload(payload_json);
            let svg = render(&payload, &test_fonts()).expect("dual-axis bar renders");

            // Paint order: bar, THEN the zero rule, THEN the line. The
            // legend's own "stroke" glyph also carries `#dea628`, so the
            // line mark is found by its own unique stroke-linecap suffix,
            // not by color alone.
            let bar_i = svg.find("fill=\"#3164a3\"").expect("base bar fill paints");
            let rule_i = svg.find("stroke=\"#8A8C8F\"").expect("zero rule paints");
            let line_i = svg
                .find("stroke=\"#dea628\" stroke-width=\"2\" stroke-linecap")
                .expect("layer line paints");
            assert!(bar_i < rule_i, "bar paints before the zero rule: {svg}");
            assert!(rule_i < line_i, "the zero rule paints BEFORE the layer under independent_y: {svg}");

            // Two grid groups: the base's own (left, dollar-ish ladder) and
            // the layer's own (right, `d3_nice(0, 4.2, 10) == 4.5`,
            // `d3_ticks(0, 4.5, 6) == [0, 1, 2, 3, 4]`).
            let grid_count = svg.matches("stroke=\"#D0D3D9\" stroke-width=\"1\"").count();
            assert!(grid_count >= 5 + 5, "both axes draw their own grid rungs: {svg}");
            assert!(svg.contains(">0<") && svg.contains(">4<"), "layer's own ladder draws 0..4: {svg}");
            // `d3_nice(0, 4.2, 10)` (d3's own fixed nice count, NOT the
            // axis's displayed `tickCount` 6) lands exactly on 4.5 — a
            // count-6 nice would land on 5 instead and draw a "5" rung
            // `d3_ticks(0, 5, 6)` never omits.
            assert!(!svg.contains(">5<"), "the layer's domain nices at count 10 (4.5), not 6 (5): {svg}");

            // The layer's own title, right-aligned past the base's own gutter.
            assert!(svg.contains("Conversion (%)"), "layer's own axis title draws: {svg}");

            // Base re-pin: its OWN tick labels ("500"/"1000"/"1500"/"2000")
            // now anchor "end" at a SMALL x (near the left edge), not past
            // the plot's right edge — proves `val_on_right` flipped to
            // `false` for the base once the layer claimed "right".
            let base_label_i = svg.find(">500<").expect("base tick label draws");
            let x_i = svg[..base_label_i].rfind("x=\"").expect("label carries an x attr");
            let x: f64 = svg[x_i + 3..].split('"').next().unwrap().parse().unwrap();
            assert!(x < 100.0, "base's own labels sit near the LEFT edge once re-pinned: x={x} in {svg}");
        }

        /// The dual-axis layer's own right axis is the bare `axis_to_vl`
        /// template with `labelAlign` dropped (`_overlay.py`'s dual-axis
        /// `layer_ay_vl`), so Vega-Lite's right-orient default applies: labels
        /// start-anchor at the theme's `labels.padding` past the plot edge
        /// (plus a visible tick's length), and only the rotated title sits
        /// past the widest label. The base's measured right-aligned gutter
        /// law never reaches the layer.
        #[test]
        fn a_dual_axis_layer_s_labels_start_anchor_at_the_theme_padding() {
            let _locale = crate::time_locale::en_us_scope();
            let payload_json = payload_with_layer(
                &[("Jan", 1000.0, 3.1), ("Feb", 1500.0, 3.4), ("Mar", 1200.0, 4.2)],
                dual_axis_layer_json(),
            );
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("dual-axis bar renders");
            let left = plot_group_left(&svg);
            let (x1, x2, _) = *horizontal_lines(&svg).first().expect("a gridline");
            let right = left + (x2 - x1);
            let label = |text: &str| -> (f64, String) {
                let i = svg.find(&format!(">{text}</text>")).expect("layer label draws");
                let tag = &svg[svg[..i].rfind("<text").unwrap()..i];
                let x: f64 = tag.split("x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
                let anchor = tag.split("text-anchor=\"").nth(1).unwrap().split('"').next().unwrap().to_string();
                (x, anchor)
            };
            for t in ["1", "2", "3", "4"] {
                let (x, anchor) = label(t);
                assert_eq!(anchor, "start", "the layer label {t} start-anchors: {svg}");
                assert!(
                    (x - (right + 0.5 + 6.0)).abs() < 0.01,
                    "the layer label {t} at x={x} sits at the theme padding past plot_right({right}): {svg}"
                );
            }
            let title_i = svg.find(">Conversion (%)</text>").expect("layer title draws");
            let tag = &svg[svg[..title_i].rfind("<text").unwrap()..title_i];
            let title_x: f64 = tag.split("x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
            let (label_x, _) = label("4");
            let widest = test_fonts().measure_shaped("4", TEST_FONT, 11.0).unwrap();
            assert!(
                title_x > label_x + widest,
                "the title sits past the widest label ({label_x} + {widest}), got {title_x}: {svg}"
            );
        }

        /// A dual axis with the theme's axis title hidden, optionally with an
        /// authored `y_label`.
        fn dual_axis_titles_svg(y_label: Option<&str>) -> String {
            let mut payload_json = payload_with_layer(
                &[("Jan", 1000.0, 3.1), ("Feb", 1500.0, 3.4), ("Mar", 1200.0, 4.2)],
                dual_axis_layer_json(),
            );
            if let Some(label) = y_label {
                payload_json["resolved"]["y_label"] = serde_json::json!(label);
            }
            render(&make_payload(payload_json), &test_fonts()).expect("dual-axis bar renders")
        }

        /// Every rotated `<text>` (an axis title) with its content.
        fn rotated_titles(svg: &str) -> Vec<(String, String)> {
            svg.split("<text ")
                .skip(1)
                .filter(|t| t.contains("rotate("))
                .map(|t| {
                    let (tag, rest) = t.split_once('>').expect("text tag closes");
                    (tag.to_string(), rest.split('<').next().expect("text content").to_string())
                })
                .collect()
        }

        /// A dual axis needs both sides labeled to tell the scales apart, so
        /// production draws both titles even when the theme hides the axis
        /// title and nothing authored a `y_label` (`_overlay.py`'s restore of
        /// the base title and its unconditional layer title). The restored
        /// titles carry no theme title styling — Vega's own defaults (bold,
        /// black, 4px padding) apply — and each reserves its own
        /// `padding + round(0.21 fs) + round(0.8 fs)` = 15px gutter.
        #[test]
        fn a_dual_axis_without_a_y_label_paints_both_titles_in_vega_default_style() {
            let svg = dual_axis_titles_svg(None);
            let titles = rotated_titles(&svg);
            assert_eq!(titles.len(), 2, "one title per axis: {svg}");
            let (base, layer) = (&titles[0], &titles[1]);
            assert!(base.0.contains("rotate(-90 ") && base.1 == "Revenue", "{base:?}");
            assert!(layer.0.contains("rotate(90 ") && layer.1 == "Conversion (%)", "{layer:?}");
            for (tag, _) in &titles {
                assert!(
                    tag.contains("font-weight=\"bold\"") && tag.contains("fill=\"#000\""),
                    "restored titles take Vega's default title style: {tag}"
                );
            }
            let suppressed = dual_axis_titles_svg(Some("Revenue"));
            assert_eq!(
                plot_group_left(&svg) - plot_group_left(&suppressed),
                15.0,
                "the restored base title reserves 4 + 2 + 9 on the left: {svg}"
            );
        }

        /// `base_measure_title_suppressed`: an authored `y_label` with the
        /// title hidden suppresses the BASE title only — nothing is restored
        /// over an author's explicit choice. The layer title is unconditional
        /// (`_overlay.py`: `layer_axis_y.title or label`), so it still paints.
        #[test]
        fn an_authored_y_label_with_a_hidden_title_suppresses_only_the_base_title() {
            let svg = dual_axis_titles_svg(Some("Revenue"));
            let titles = rotated_titles(&svg);
            assert_eq!(titles.len(), 1, "only the layer title: {svg}");
            let (tag, text) = &titles[0];
            assert!(tag.contains("rotate(90 ") && text == "Conversion (%)", "{tag} {text}");
            assert!(tag.contains("fill=\"#000\""), "{tag}");
        }

        /// A layer over a grouped base: production's compiled spec gives the
        /// layer NO `x`/`xOffset` encoding of its own (`_overlay.py`'s
        /// `layer_ay_vl`/shared-scale template), so it inherits the TOP-LEVEL
        /// `x` — the outer, ungrouped band, not either sub-band — and no
        /// `data` override either, so Vega draws the line straight through
        /// EVERY row of the base's own (repeated-per-sub-series) dataset,
        /// never deduplicating (probed: `grouped-bar-with-layer`'s compiled
        /// `layer[1]` carries no `data` key at all). `build_data_map`'s
        /// one-row-per-(category, series) contract is therefore the wrong
        /// shape for this layer's OWN data read: `paint_layer_marks` must
        /// walk `payload.rows` directly (one point per ROW, in row order),
        /// not `cat_domain` (one point per unique category).
        #[test]
        fn a_layer_on_a_grouped_base_anchors_at_the_outer_bands_own_center() {
            let mut payload_json = make_vertical_payload(&[("Jan", 100.0), ("Feb", 150.0)], true);
            // Duplicate `target` across both sub-series rows per month —
            // exactly what a real query returns when `quota` (or `target`)
            // does not vary by the base's own `color` field.
            payload_json["rows"] = serde_json::json!([
                {"product": "Jan", "region": "North", "revenue": 100.0, "target": 90.0},
                {"product": "Jan", "region": "South", "revenue": 60.0, "target": 90.0},
                {"product": "Feb", "region": "North", "revenue": 150.0, "target": 140.0},
                {"product": "Feb", "region": "South", "revenue": 90.0, "target": 140.0},
            ]);
            payload_json["resolved"]["layers"] = serde_json::json!([line_layer_json("#dea628")]);
            let payload = make_payload(payload_json);
            let svg = render(&payload, &test_fonts()).expect("layer over a grouped base renders");

            // The line's path carries 4 points (one per ROW, not 2 for the
            // 2 unique months) — two coincident pairs, each pair landing on
            // the SAME x (the outer band's own center for that month, not
            // either sub-band's `xOffset` position).
            let path_i = svg
                .find("fill=\"none\" stroke=\"#dea628\"")
                .expect("layer line paints (not the legend's own stroke swatch)");
            let d_start = svg[..path_i].rfind("d=\"").expect("path carries a d attr") + 3;
            let d = &svg[d_start..svg[d_start..].find('"').unwrap() + d_start];
            let xs: Vec<f64> = d
                .split(|c| c == 'M' || c == 'L')
                .filter(|s| !s.is_empty())
                .map(|pt| pt.split(',').next().unwrap().parse().unwrap())
                .collect();
            assert_eq!(xs.len(), 4, "one point per ROW, duplicates included: {d}");
            assert!((xs[0] - xs[1]).abs() < 0.01, "Jan's two rows share one x: {xs:?}");
            assert!((xs[2] - xs[3]).abs() < 0.01, "Feb's two rows share one x: {xs:?}");
            assert!(xs[0] < xs[2], "Jan's band center sits left of Feb's: {xs:?}");
        }

        /// A grouped base's mixed-mark legend is the grouped legend's OWN
        /// per-series entries (one square per `color` domain value, the
        /// SAME entries the non-layered grouped-bar path already builds
        /// via `stacked_legend_entries`) plus the layer's stroke entry
        /// appended — never the base's single y-field-labeled swatch that
        /// arm.rs's original 2-entry hardcode assumed. Probed:
        /// `grouped-bar-with-layer`'s reference legend carries 3 values
        /// ("Enterprise, Mid-Market, Quota"), every square solid-filled in
        /// its OWN series color (`_mixed_mark_legend_symbols`'s
        /// `symbolStrokeColor` — once ANY entry strokes, every entry does).
        #[test]
        fn a_layer_on_a_grouped_base_keeps_the_grouped_legends_own_entries() {
            let mut payload_json = make_vertical_payload(&[("Jan", 100.0), ("Feb", 150.0)], true);
            payload_json["rows"] = serde_json::json!([
                {"product": "Jan", "region": "North", "revenue": 100.0, "target": 90.0},
                {"product": "Jan", "region": "South", "revenue": 60.0, "target": 90.0},
                {"product": "Feb", "region": "North", "revenue": 150.0, "target": 140.0},
                {"product": "Feb", "region": "South", "revenue": 90.0, "target": 140.0},
            ]);
            payload_json["resolved"]["layers"] = serde_json::json!([line_layer_json("#dea628")]);
            let payload = make_payload(payload_json);
            let svg = render(&payload, &test_fonts()).expect("layer over a grouped base renders");

            assert!(svg.contains(">North<"), "the grouped legend's own first entry: {svg}");
            assert!(svg.contains(">South<"), "the grouped legend's own second entry: {svg}");
            assert!(svg.contains(">Target<"), "the layer's own entry: {svg}");
            // Both grouped entries paint as solid squares in their OWN
            // series fill, not the theme's single-series fill — the
            // color scale's own two slots.
            assert!(svg.contains("fill=\"#3164a3\" stroke=\"#3164a3\""), "North's own solid square: {svg}");
            assert!(svg.contains("fill=\"#779bc9\" stroke=\"#779bc9\""), "South's own solid square: {svg}");
            assert!(svg.contains("d=\"M-10,0L10,0\""), "the layer's own stroke glyph: {svg}");
        }

        /// One shared scale: a scatter layer after a bar layer takes the next
        /// free color and its own legend row.
        #[test]
        fn a_scatter_layer_after_a_bar_layer_takes_its_own_color_and_legend_entry() {
            let mut scatter = scatter_layer_json(false);
            scatter["point_mark"]["color"] = serde_json::Value::Null;
            scatter["ink"] = serde_json::json!("#ad9c7f");
            scatter["label"] = serde_json::json!("Marker");
            let mut payload_json = payload_with_layer(&[("Jan", 100.0, 40.0)], bar_layer_json(None));
            payload_json["resolved"]["layers"] = serde_json::json!([bar_layer_json(None), scatter]);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert!(svg.contains("fill=\"#779bc9\" stroke=\"#FFFFFF\""), "the bar layer takes palette[1]: {svg}");
            assert!(svg.contains("fill=\"#ad9c7f\""), "the scatter takes palette[2]: {svg}");
            assert!(svg.contains(">Marker<"), "and its legend row: {svg}");
        }

        #[test]
        fn a_scatter_layer_labeled_like_a_grouped_base_series_is_rejected_by_name() {
            let mut scatter = scatter_layer_json(false);
            scatter["label"] = serde_json::json!("North");
            let mut payload_json = make_vertical_payload(&[("Jan", 100.0)], true);
            payload_json["rows"] = serde_json::json!([
                {"product": "Jan", "region": "North", "revenue": 100.0, "target": 3.0},
                {"product": "Jan", "region": "South", "revenue": 60.0, "target": 3.0},
            ]);
            payload_json["resolved"]["layers"] = serde_json::json!([scatter]);
            let err = render(&make_payload(payload_json), &test_fonts()).unwrap_err();
            assert_eq!(err.code, "ERR-LAYER-LABEL-COLLISION", "{err}");
            assert_eq!(err.params["label"], "North");
        }

        /// `ERR_LAYER_LABEL_COLLISION`: a layer label equal to a series of a
        /// color-split base would merge two legend rows, so production raises.
        #[test]
        fn a_layer_label_equal_to_a_grouped_base_series_is_rejected_by_name() {
            let mut payload_json = make_vertical_payload(&[("Jan", 100.0)], true);
            let mut layer = line_layer_json("#dea628");
            layer["label"] = serde_json::json!("North");
            payload_json["rows"] = serde_json::json!([
                {"product": "Jan", "region": "North", "revenue": 100.0, "target": 90.0},
                {"product": "Jan", "region": "South", "revenue": 60.0, "target": 90.0},
            ]);
            payload_json["resolved"]["layers"] = serde_json::json!([layer]);
            let err = render(&make_payload(payload_json), &test_fonts()).unwrap_err();
            assert_eq!(err.code, "ERR-LAYER-LABEL-COLLISION", "{err}");
            assert_eq!(err.params["label"], "North");
        }

        /// `_overlay.py:1584-1592`: an unauthored layer's fill is the NEXT
        /// slot in the shared color scale — `layer_ord = len(scale_domain)`,
        /// where `scale_domain` is seeded with the base's OWN color domain
        /// (one entry per grouped series), then `palette[layer_ord %
        /// len(palette)]`. On a 2-entry grouped base that's `palette[2]`,
        /// NOT `palette[1]` (the single-series base's own slot — seeded with
        /// just the one base-label entry there, so `layer_ord == 1`).
        #[test]
        fn an_unauthored_layer_color_on_a_grouped_base_takes_the_domains_next_slot() {
            let mut payload_json = make_vertical_payload(&[("Jan", 100.0), ("Feb", 150.0)], true);
            payload_json["rows"] = serde_json::json!([
                {"product": "Jan", "region": "North", "revenue": 100.0, "target": 90.0},
                {"product": "Jan", "region": "South", "revenue": 60.0, "target": 90.0},
                {"product": "Feb", "region": "North", "revenue": 150.0, "target": 140.0},
                {"product": "Feb", "region": "South", "revenue": 90.0, "target": 140.0},
            ]);
            let mut layer = line_layer_json("#dea628");
            layer["ink"] = serde_json::json!("#ad9c7f");
            layer["line_mark"]["stroke"]["color"] = serde_json::Value::Null;
            layer["point_mark"]["color"] = serde_json::Value::Null;
            payload_json["resolved"]["layers"] = serde_json::json!([layer]);
            let payload = make_payload(payload_json);
            let svg = render(&payload, &test_fonts()).expect("layer over a grouped base renders");

            assert!(
                svg.contains("fill=\"none\" stroke=\"#ad9c7f\""),
                "unauthored layer color is the base's 2-entry domain's next slot (palette[2]), not palette[1]: {svg}"
            );
        }

        /// `_fix_bar_band_width` (`_overlay.py:778`) only runs inside a
        /// LAYERED spec — it rewrites the base bar's `{"band": f}` width
        /// shorthand to an explicit `f * bandwidth('xOffset')`, so BOTH the
        /// mark width and its centering offset resolve against the OFFSET
        /// scale's own bandwidth (`sub`, since `xOffset.paddingInner` is
        /// hardcoded 0), not the outer category bandwidth (`0.8 * step`) a
        /// non-layered grouped bar uses (`two_series_take_the_partial_overlap`,
        /// no layer). Probed: `grouped-bar-with-layer`'s reference
        /// Jan/Enterprise bar sits at x=26.671875 in a 71.125px sub-step —
        /// `0.2*step - 0.125*71.125`, not `0.2*step - 0.125*(0.8*step)`.
        #[test]
        fn a_layered_grouped_base_positions_its_sub_bands_against_the_offset_scale_not_the_outer_one() {
            let mut payload_json = make_vertical_payload(&[("Jan", 100.0), ("Feb", 150.0)], true);
            payload_json["resolved"]["style"]["mark"]["border"]["radius"] = serde_json::json!(0.0);
            payload_json["rows"] = serde_json::json!([
                {"product": "Jan", "region": "North", "revenue": 100.0, "target": 90.0},
                {"product": "Jan", "region": "South", "revenue": 60.0, "target": 90.0},
                {"product": "Feb", "region": "North", "revenue": 150.0, "target": 140.0},
                {"product": "Feb", "region": "South", "revenue": 90.0, "target": 140.0},
            ]);
            payload_json["resolved"]["layers"] = serde_json::json!([line_layer_json("#dea628")]);
            let payload = make_payload(payload_json);
            let svg = render(&payload, &test_fonts()).expect("layer over a grouped base renders");
            let bands = bar_bands(&svg, false);
            assert_eq!(bands.len(), 4, "{bands:?}");
            let sub = bands[1].0 - bands[0].0;
            let step = bands[2].0 - bands[0].0;
            assert!((bands[0].1 - 1.25 * sub).abs() < 0.02, "1.25 x sub-step: {bands:?}");
            assert!(
                (bands[0].0 - (0.2 * step - 0.125 * sub)).abs() < 0.02,
                "offset-scale-based centering, not the outer band: {bands:?}"
            );
        }

        /// A horizontal bar's bar layer grows from the zero rule on the shared value
        /// scale, at the next palette slot, one rect per row on the row's own band.
        #[test]
        fn a_bar_layer_on_a_horizontal_base_paints_its_own_bars_on_the_shared_scale() {
            let mut layer = bar_layer_json(None);
            layer["bar_mark"]["labels"] = serde_json::json!(null);
            layer["bar_mark"]["border"]["radius"] = serde_json::json!(0.0);
            let mut payload_json = payload_with_layer(&[("Jan", 100.0, 40.0), ("Feb", 120.0, 60.0)], layer);
            payload_json["resolved"]["orientation"] = serde_json::json!("horizontal");
            payload_json["resolved"]["style"]["mark"]["border"]["radius"] = serde_json::json!(0.0);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            let mut bars: Vec<_> = parse_rects(&svg).into_iter().filter(|r| r.4 == "#779bc9" && r.3 != 10.0).collect();
            bars.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap());
            assert_eq!(bars.len(), 2, "one layer bar per row: {svg}");
            let (feb, jan) = (&bars[0], &bars[1]);
            assert!((jan.2 / feb.2 - 40.0 / 60.0).abs() < 1e-9, "widths follow the layer's own values: {bars:?}");
            assert_eq!(jan.0, feb.0, "both grow from the zero rule: {bars:?}");
            assert!(jan.3 > 0.0 && jan.3 == feb.3, "{bars:?}");
        }

        fn horizontal_bar_layer_payload(layer: serde_json::Value, rows: &[(&str, f64, f64)]) -> serde_json::Value {
            let mut payload_json = payload_with_layer(rows, layer);
            payload_json["resolved"]["orientation"] = serde_json::json!("horizontal");
            payload_json["resolved"]["style"]["mark"]["border"]["radius"] = serde_json::json!(0.0);
            payload_json
        }

        /// An outline layer (`style.marks.bar.opacity: 0`) paints a transparent fill under
        /// its opaque border, in either orientation: a solid one hides the series behind it.
        #[test]
        fn a_bar_layer_with_zero_opacity_paints_an_outline_only() {
            for orientation in ["horizontal", "vertical"] {
                let mut layer = bar_layer_json(None);
                layer["bar_mark"]["labels"] = serde_json::json!(null);
                layer["bar_mark"]["border"] = serde_json::json!({"width": 4.0, "color": "#d49656", "radius": 0.0});
                layer["bar_mark"]["opacity"] = serde_json::json!(0.0);
                let mut payload_json = payload_with_layer(&[("Jan", 100.0, 40.0)], layer);
                payload_json["resolved"]["orientation"] = serde_json::json!(orientation);
                payload_json["resolved"]["style"]["mark"]["border"]["radius"] = serde_json::json!(0.0);
                let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
                let outlined: Vec<&str> = svg.split('<').filter(|e| e.contains("stroke=\"#d49656\"")).collect();
                assert!(!outlined.is_empty(), "{orientation}: {svg}");
                assert!(
                    outlined[0].contains("fill=\"#779bc9\" fill-opacity=\"0\""),
                    "{orientation}: the first pass fills at zero opacity: {}",
                    outlined[0]
                );
            }
        }

        /// The layer's labels sit on its own bars: the plot's translate is in the
        /// group, not in the text.
        #[test]
        fn a_horizontal_bar_layers_value_labels_sit_at_the_middle_of_its_bars() {
            let mut layer = bar_layer_json(None);
            layer["bar_mark"]["border"]["radius"] = serde_json::json!(0.0);
            let payload_json = horizontal_bar_layer_payload(layer, &[("Jan", 100.0, 90.0), ("Feb", 120.0, 110.0)]);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            let jan = parse_rects(&svg).into_iter().filter(|r| r.4 == "#779bc9" && r.3 != 10.0).max_by(|a, b| a.1.partial_cmp(&b.1).unwrap()).unwrap();
            let label = svg.split(">90</text>").next().unwrap().rsplit("<text x=\"").next().unwrap();
            let label_x: f64 = label.split('"').next().unwrap().parse().unwrap();
            assert!((label_x - (jan.0 + jan.2 / 2.0)).abs() < 0.01, "{label_x} vs {jan:?}");
        }

        /// A layer grouped by its own color stacks its values left to right in domain
        /// order, each on the color it claimed.
        #[test]
        fn a_color_grouped_horizontal_bar_layer_stacks_left_to_right_in_domain_order() {
            let mut layer = bar_layer_json(Some("segs"));
            layer["bar_mark"]["labels"] = serde_json::json!(null);
            layer["bar_mark"]["border"]["radius"] = serde_json::json!(0.0);
            layer["color"] = serde_json::json!("seg");
            layer["ink"] = serde_json::Value::Null;
            layer["series_inks"] = serde_json::json!({"a": "#779bc9", "b": "#ad9c7f"});
            let mut payload_json = horizontal_bar_layer_payload(layer, &[("Jan", 100.0, 0.0)]);
            payload_json["datasets"] = serde_json::json!({"segs": [
                {"product": "Jan", "target": 30.0, "seg": "a"},
                {"product": "Jan", "target": 20.0, "seg": "b"},
            ]});
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            let rects = parse_rects(&svg);
            let find = |fill: &str| rects.iter().find(|r| r.4 == fill && r.3 != 10.0).unwrap().clone();
            let (a, b) = (find("#779bc9"), find("#ad9c7f"));
            assert_eq!(a.0, 0.0, "{a:?}");
            assert!((b.0 - (a.0 + a.2)).abs() < 0.011, "b starts where a ends: {a:?} {b:?}");
            assert!((a.2 / b.2 - 30.0 / 20.0).abs() < 0.01, "{a:?} {b:?}");
        }

        /// A stack that outgrows the shared domain paints past the last gridline; the
        /// fit grows to hold it, it is neither clamped nor refused.
        #[test]
        fn a_horizontal_bar_layer_stack_past_the_domain_paints_past_the_plot() {
            let mut layer = bar_layer_json(Some("big"));
            layer["bar_mark"]["labels"] = serde_json::json!(null);
            layer["bar_mark"]["border"]["radius"] = serde_json::json!(0.0);
            let mut payload_json = horizontal_bar_layer_payload(layer, &[("Jan", 100.0, 40.0)]);
            payload_json["datasets"] = serde_json::json!({"big": [
                {"product": "Jan", "target": 1500.0},
                {"product": "Jan", "target": 1500.0},
            ]});
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            let last_grid = svg
                .split("<line x1=\"")
                .skip(1)
                .filter_map(|l| l.split('"').next()?.parse::<f64>().ok())
                .fold(0.0_f64, f64::max);
            let stacked = parse_rects(&svg).into_iter().filter(|r| r.4 == "#779bc9" && r.3 != 10.0).map(|r| r.0 + r.2).fold(0.0_f64, f64::max);
            assert!(stacked > last_grid, "{stacked} vs {last_grid}");
        }

        /// A colored ranged horizontal base takes a bar layer on its shared scale.
        #[test]
        fn a_colored_ranged_horizontal_base_takes_a_bar_layer() {
            let mut payload_json = make_vertical_payload(&[("Jan", 100.0)], true);
            payload_json["resolved"]["orientation"] = serde_json::json!("horizontal");
            payload_json["resolved"]["y_start"] = serde_json::json!("low");
            payload_json["resolved"]["layers"] = serde_json::json!([bar_layer_json(None)]);
            for row in payload_json["rows"].as_array_mut().unwrap() {
                row["low"] = serde_json::json!(10.0);
                row["target"] = serde_json::json!(60.0);
            }
            render(&make_payload(payload_json), &test_fonts()).expect("renders");
        }

        /// A zero-size scatter layer draws zero-radius points, as production's
        /// `M0,0` symbols; a payload with no size at all is malformed.
        #[test]
        fn a_scatter_layer_of_size_zero_draws_zero_radius_points() {
            let mut layer = scatter_layer_json(false);
            layer["point_mark"]["size"] = serde_json::json!(0.0);
            let cats = [("Jan", 100.0, 41.5), ("Feb", 120.0, 63.5)];
            let svg = render(&make_payload(payload_with_layer(&cats, layer.clone())), &test_fonts()).expect("renders");
            assert_eq!(svg.matches("r=\"0.000\"").count(), 2, "{svg}");
            layer["point_mark"]["size"] = serde_json::json!(-1.0);
            let err = render(&make_payload(payload_with_layer(&cats, layer.clone())), &test_fonts()).unwrap_err();
            assert!(err.contains("negative"), "{err}");
            layer["point_mark"].as_object_mut().unwrap().remove("size");
            let err = render(&make_payload(payload_with_layer(&cats, layer)), &test_fonts()).unwrap_err();
            assert!(err.contains("point_mark.size"), "{err}");
        }

        /// A scatter layer paints its own points between the bars and the
        /// base's value labels, so the label group's ordinal is unknown
        /// there: the labels stay bare `<text>`, never a stamped group under
        /// an invented ordinal. The layer's own points still carry their
        /// description, placed by transform.
        #[test]
        fn value_labels_under_a_scatter_layer_stay_bare() {
            let layer = serde_json::json!({
                "type": "scatter",
                "point_mark": {
                    "size": 64.0, "color": "#dea628", "filled": true, "fill": null,
                    "stroke_width": 2.0, "labels": {"visible": false}
                },
                "axis_y": {"position": null},
                "x": null, "y": "target", "color": null, "ink": "#779bc9", "query_name": null
            });
            let mut payload_json =
                payload_with_layer(&[("Jan", 100.0, 90.0), ("Feb", 150.0, 140.0)], layer);
            payload_json["resolved"]["legend"]["visible"] = serde_json::json!(false);
            payload_json["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.0f");
            payload_json["resolved"]["style"]["mark"]["labels"] = serde_json::json!({
                "visible": true, "field": null, "format": ",.0f", "dx": null,
                "dy": null, "font": null, "position": null
            });
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert!(svg.contains(">150<"), "the label paints: {svg}");
            assert!(!svg.contains("data-dbt-value-label"), "{svg}");
            assert!(
                svg.contains("<circle transform=\"translate(")
                    && svg.contains("aria-roledescription=\"point\""),
                "the layer's points are described: {svg}"
            );
        }

        /// The one modeled shape: a single-series vertical bar with a
        /// shared-y-scale `line` layer. Paint order is bar → line → zero
        /// rule (the rule paints LAST once a second layer exists — see the
        /// paint-order comment at the call site), the legend carries two
        /// entries (a square swatch for the bar, a stroke circle for the
        /// line), and the line's own row values (not the bar's) drive its
        /// path.
        #[test]
        fn shared_y_scale_line_layer_paints_after_the_bar_and_before_the_zero_rule() {
            let payload_json = payload_with_layer(
                &[("Jan", 100.0, 90.0), ("Feb", 150.0, 140.0), ("Mar", 120.0, 130.0)],
                line_layer_json("#dea628"),
            );
            let payload = make_payload(payload_json);
            let svg = render(&payload, &test_fonts()).expect("layered bar renders");

            let bar_i = svg.find("fill=\"#3164a3\"").expect("base bar fill paints");
            let line_i = svg.find("stroke=\"#dea628\" stroke-width=\"2\"").expect("layer line paints");
            let rule_i = svg.rfind("<line ").expect("zero rule paints");
            assert!(bar_i < line_i, "bar paints before the layer: {svg}");
            assert!(line_i < rule_i, "the zero rule paints LAST once a layer exists: {svg}");

            // Two legend entries: the base's own field name ("revenue") with
            // a square swatch, the layer's own field name ("target") with a
            // "stroke" glyph (a line segment, not a circle) — mirrors
            // `_mixed_mark_legend_symbols`.
            assert!(svg.contains(">Revenue<"), "base legend label: {svg}");
            assert!(svg.contains(">Target<"), "layer legend label: {svg}");
            assert!(svg.contains("<rect"), "base entry keeps a square swatch: {svg}");
            assert!(svg.contains("d=\"M-10,0L10,0\""), "layer entry paints a stroke glyph: {svg}");

            // The line's own hollow point (filled: false) at Feb's value
            // (140, not the bar's own 150) proves it reads the layer's row
            // field, not the base's.
            assert!(
                svg.contains("<circle transform=\"translate(364.500,365.490)\""),
                "hollow point at the layer's own (Feb, 140) value, positioned by transform \
                 the way Vega positions a symbol: {svg}"
            );
        }

        /// A layer authoring both a cross-query `query_name` and its own
        /// nominal `color` groups its own rows by that color field and
        /// draws one line per distinct value, each taking the shared
        /// color scale's next unclaimed palette slot after the base's own
        /// (slot 0) — mirrors `_overlay.py:1546-1562` and this task's
        /// probed `revenue_vs_regional_targets` row (no `style` overrides
        /// authored at all, matching the doc example this pins:
        /// `type: line, query: regional_targets, y: target, color: region`).
        #[test]
        fn a_color_grouped_cross_query_layer_yields_two_series_and_two_stroke_legend_entries() {
            let mut payload_json =
                make_vertical_payload(&[("Jan", 100.0), ("Feb", 150.0), ("Mar", 120.0)], false);
            payload_json["resolved"]["query_name"] = serde_json::json!("base_query");
            let layer = serde_json::json!({
                "type": "line",
                "line_mark": {
                    "disconnected_width": 0.9,
                    "stroke": {"width": 2.5, "color": null, "cap": "round", "join": "round"},
                    "halo_multiplier": 2.0,
                    "curve": "linear",
                    "labels": {"visible": false}
                },
                "point_mark": {
                    "size": null, "color": null, "filled": true, "fill": "#ffffff",
                    "stroke_width": 2.5, "labels": {"visible": false}
                },
                "axis_y": {"position": null},
                "x": null,
                "y": "target",
                "label": null,
                "color": "region",
                "ink": null,
                "series_inks": {"East": "#779bc9", "West": "#ad9c7f"},
                "query_name": "targets_query"
            });
            payload_json["resolved"]["layers"] = serde_json::json!([layer]);
            payload_json["resolved"]["legend"]["visible"] = serde_json::json!(true);
            payload_json["datasets"] = serde_json::json!({
                "targets_query": [
                    {"product": "Jan", "target": 90.0, "region": "East"},
                    {"product": "Jan", "target": 80.0, "region": "West"},
                    {"product": "Feb", "target": 140.0, "region": "East"},
                    {"product": "Feb", "target": 130.0, "region": "West"},
                    {"product": "Mar", "target": 130.0, "region": "East"},
                    {"product": "Mar", "target": 110.0, "region": "West"},
                ]
            });
            let payload = make_payload(payload_json);
            let svg =
                render(&payload, &test_fonts()).expect("color-grouped cross-query layer renders");

            // palette[0] (#3164a3) is the base's own single-series fill —
            // East/West take the next two unclaimed slots, #779bc9/#ad9c7f
            // on this fixture's own test palette.
            assert!(svg.contains("stroke=\"#779bc9\""), "East line takes palette slot 1: {svg}");
            assert!(svg.contains("stroke=\"#ad9c7f\""), "West line takes palette slot 2: {svg}");

            assert!(svg.contains(">Revenue<"), "base legend label: {svg}");
            assert!(svg.contains(">East<"), "East legend label: {svg}");
            assert!(svg.contains(">West<"), "West legend label: {svg}");
            let east_i = svg.find(">East<").expect("East legend entry");
            let west_i = svg.find(">West<").expect("West legend entry");
            assert!(east_i < west_i, "East (alphabetical) before West: {svg}");
            assert_eq!(
                svg.matches("d=\"M-10,0L10,0\"").count(),
                2,
                "one stroke legend glyph per color value: {svg}"
            );
        }

        /// A cross-query layer with no color of its own still reads ITS
        /// OWN dataset's rows, not the base's — the base's `revenue` rows
        /// never change between the two renders below, so a differing
        /// output SVG proves the layer's path came from
        /// `payload.datasets["targets_query"]`, not a silent fallback to
        /// `payload.rows`.
        #[test]
        fn a_cross_query_layer_with_no_color_reads_its_own_datasets_rows() {
            let build = |feb_target: f64| -> String {
                let mut payload_json = make_vertical_payload(
                    &[("Jan", 100.0), ("Feb", 150.0), ("Mar", 120.0)],
                    false,
                );
                payload_json["resolved"]["query_name"] = serde_json::json!("base_query");
                let mut layer = line_layer_json("#dea628");
                layer["query_name"] = serde_json::json!("targets_query");
                payload_json["resolved"]["layers"] = serde_json::json!([layer]);
                payload_json["resolved"]["legend"]["visible"] = serde_json::json!(true);
                payload_json["datasets"] = serde_json::json!({
                    "targets_query": [
                        {"product": "Jan", "target": 90.0},
                        {"product": "Feb", "target": feb_target},
                        {"product": "Mar", "target": 130.0},
                    ]
                });
                render(&make_payload(payload_json), &test_fonts())
                    .expect("cross-query layer with no color renders")
            };
            let low = build(90.0);
            let high = build(1900.0);
            assert_ne!(
                low, high,
                "the layer's own dataset rows drive its path, not the base's unchanged revenue rows"
            );
            assert!(low.contains(">Target<"), "layer legend label still shown: {low}");
        }

        /// A shared-scale layer merges the base's y encoding into a second
        /// `layer[]` entry sharing one Vega-Lite scale, which flips the
        /// compiled scale's `nice` default from unset (an un-nested baked
        /// `domainMax` renders EXACT) to `true` — extending an off-round
        /// baked `domainMax` (1720, from `axis_y.ticks.count: 6`) to
        /// `d3_nice(0, 1720, 6) == 1800`, probed against
        /// `column_line_combo_review`'s own reference (baked 165240 →
        /// rendered 180000). Removing the fix makes the bar top the SAME
        /// whether or not a layer is present; with it, the layered bar is
        /// shorter (a bigger domain for the same value).
        #[test]
        fn a_shared_scale_layer_nices_the_bakeds_domain_max_past_its_own_headroom() {
            let mut layered_json =
                payload_with_layer(&[("Jan", 1000.0, 900.0)], line_layer_json("#dea628"));
            layered_json["resolved"]["style"]["axis_y"]["domain_max"] = serde_json::json!(1720.0);
            let svg = render(&make_payload(layered_json), &test_fonts()).expect("layered bar renders");

            // `plot_h`: the zero rule's own y (the plot's local bottom edge,
            // v=0) — read straight from THIS render rather than assumed, so
            // the check is immune to the legend/title ink a layer also adds.
            let plot_h: f64 = svg
                .split("<line x1=\"0.00\" y1=\"")
                .nth(1)
                .and_then(|s| s.split('.').next())
                .expect("zero rule carries the plot's local bottom edge")
                .parse()
                .expect("zero rule y is numeric");
            let bar_top: f64 = svg
                .split("V ")
                .nth(1)
                .and_then(|s| s.split(' ').next())
                .expect("bar path carries a top V coordinate")
                .parse()
                .expect("bar top V is numeric");

            // `make_vertical_payload`'s own `mark.border.radius` (4.0): the
            // path's own "V" coordinate is the rounded corner's START, one
            // radius below the bar's true flat top.
            let corner_radius = 4.0;
            let niced_top = plot_h * (1.0 - 1000.0 / 1800.0) + corner_radius;
            let raw_top = plot_h * (1.0 - 1000.0 / 1720.0) + corner_radius;
            assert!(
                (bar_top - niced_top).abs() < 1.0,
                "bar top {bar_top} should sit at the NICED domain (1800)'s {niced_top}: {svg}"
            );
            assert!(
                (bar_top - raw_top).abs() > 3.0,
                "bar top {bar_top} should NOT sit at the raw baked domain (1720)'s {raw_top} \
                 — the shared-scale layer must have niced it: {svg}"
            );
        }

        /// The nice above runs at d3's own FIXED count (10), never the
        /// axis's own displayed `tickCount` — the SAME distinction the
        /// dual-axis layer's own domain law already draws
        /// (`d3_nice(0, max, 10)`, not the axis's `tickCount`). Probed on
        /// `grouped-bar-with-layer` (`ax_y.ticks.count` bakes 6, baked
        /// `domainMax` 248.4): `d3_nice(0, 248.4, 6) == 250` — the port's
        /// pre-fix, WRONG value — but the reference's own axis reads
        /// "values from 0 to 260", `d3_nice(0, 248.4, 10) == 260` exactly.
        /// The two counts coincide at 1720 (both nice to 1800), which is
        /// why the sibling test above never caught this.
        #[test]
        fn a_shared_scale_layer_nices_at_d3s_fixed_count_ten_not_the_axis_tickcount() {
            let mut layered_json =
                payload_with_layer(&[("Jan", 100.0, 90.0)], line_layer_json("#dea628"));
            layered_json["resolved"]["style"]["axis_y"]["domain_max"] = serde_json::json!(248.4);
            // The real fixture's own baked ladder — one rung (250) sits
            // PAST the un-niced 248.4 max, filtered out by
            // `value_domain_and_ticks`, then readmitted once the
            // shared-scale nice extends the domain to 260.
            layered_json["resolved"]["style"]["axis_y"]["tick_values"] =
                serde_json::json!([0.0, 50.0, 100.0, 150.0, 200.0, 250.0]);
            let svg = render(&make_payload(layered_json), &test_fonts()).expect("layered bar renders");

            let grid_count = svg.matches("stroke=\"#D0D3D9\" stroke-width=\"1\"").count();
            assert_eq!(grid_count, 6, "the 250 rung is readmitted, not dropped: {svg}");

            let plot_h: f64 = svg
                .split("<line x1=\"0.00\" y1=\"")
                .nth(1)
                .and_then(|s| s.split('.').next())
                .expect("zero rule carries the plot's local bottom edge")
                .parse()
                .expect("zero rule y is numeric");
            let bar_top: f64 = svg
                .split("V ")
                .nth(1)
                .and_then(|s| s.split(' ').next())
                .expect("bar path carries a top V coordinate")
                .parse()
                .expect("bar top V is numeric");

            let corner_radius = 4.0;
            let niced_top_count_10 = plot_h * (1.0 - 100.0 / 260.0) + corner_radius;
            let niced_top_count_6 = plot_h * (1.0 - 100.0 / 250.0) + corner_radius;
            assert!(
                (bar_top - niced_top_count_10).abs() < 1.0,
                "bar top {bar_top} should sit at d3's fixed-count-10 nice (260)'s \
                 {niced_top_count_10}: {svg}"
            );
            assert!(
                (bar_top - niced_top_count_6).abs() > 1.0,
                "bar top {bar_top} should NOT sit at the axis's own tickCount-6 nice \
                 (250)'s {niced_top_count_6}: {svg}"
            );
        }

        /// Ladder rung 1 (multi-layer): two colorless `type: "bar"` layers
        /// over an ungrouped base, each claiming the shared scale's next
        /// unclaimed palette slot in AUTHORED order — the base at slot 0,
        /// the first layer at slot 1, the second at slot 2. Production
        /// admits any number of layers (`render_cartesian_overlay(layers:
        /// tuple[ResolvedLayer, ...], ...)`'s own `for layer_idx, layer in
        /// enumerate(layers)`); this is the port's own minimal two-layer
        /// rung, ahead of `charts/extensibility_1`'s `bullet` (bar + line).
        #[test]
        fn two_bar_type_layers_paint_at_sequential_palette_slots() {
            let mut layer2 = bar_layer_json(None);
            layer2["ink"] = serde_json::json!("#ad9c7f");
            layer2["y"] = serde_json::json!("revenue");
            let mut payload_json = payload_with_layer(&[("Jan", 100.0, 90.0)], bar_layer_json(None));
            payload_json["resolved"]["layers"] = serde_json::json!([bar_layer_json(None), layer2]);
            let payload = make_payload(payload_json);
            let svg = render(&payload, &test_fonts())
                .expect("two colorless bar-type layers over an ungrouped base render");
            assert!(svg.contains("fill=\"#3164a3\""), "base bar still paints: {svg}");
            assert!(svg.contains("fill=\"#779bc9\""), "first layer paints at slot 1: {svg}");
            assert!(svg.contains("fill=\"#ad9c7f\""), "second layer paints at slot 2: {svg}");
        }

        /// Ladder rung 2 (multi-layer): a colorless `type: "bar"` layer
        /// followed by a colorless `type: "line"` layer, in that authored
        /// order, the exact shape `bullet`'s own two layers
        /// use (minus the base's own `color` grouping and the line's
        /// `curve: "step"`).
        #[test]
        fn a_bar_layer_then_a_trailing_line_layer_render_together() {
            let mut payload_json = payload_with_layer(&[("Jan", 100.0, 90.0)], bar_layer_json(None));
            let mut line = line_layer_json("#dea628");
            line["label"] = serde_json::json!("Goal");
            payload_json["resolved"]["layers"] = serde_json::json!([bar_layer_json(None), line]);
            let payload = make_payload(payload_json);
            let svg = render(&payload, &test_fonts())
                .expect("a bar layer followed by a trailing line layer renders");
            assert!(svg.contains("fill=\"#3164a3\""), "base bar still paints: {svg}");
            assert!(svg.contains("fill=\"#779bc9\""), "the bar layer paints at slot 1: {svg}");
            assert!(
                svg.contains("stroke=\"#dea628\""),
                "the trailing line layer paints at its own authored stroke color: {svg}"
            );
        }

        /// A `[line, bar]` array: production paints and claims palette slots in
        /// authored order, so the line takes slot 1 and the bar slot 2, and the bar
        /// paints over the line.
        #[test]
        fn a_bar_layer_after_a_line_layer_paints_over_it_and_claims_the_later_slot() {
            let mut payload_json = payload_with_layer(&[("Jan", 100.0, 90.0)], bar_layer_json(None));
            let mut line = line_layer_json("#dea628");
            line["color"] = serde_json::Value::Null;
            line["line_mark"]["stroke"]["color"] = serde_json::Value::Null;
            line["ink"] = serde_json::json!("#779bc9");
            let mut bar = bar_layer_json(None);
            bar["ink"] = serde_json::json!("#ad9c7f");
            payload_json["resolved"]["layers"] = serde_json::json!([line, bar]);
            payload_json["resolved"]["palette"] = serde_json::json!(["#3164a3", "#779bc9", "#ad9c7f"]);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            let line_i = svg.find("stroke=\"#779bc9\" stroke-width=\"2\" stroke-linecap").expect("line at slot 1");
            let bar_i = svg.find("fill=\"#ad9c7f\"").expect("bar layer at slot 2");
            assert!(line_i < bar_i, "the later layer paints over the earlier: {svg}");
        }

        // ── Bar-type layer over a `color`-grouped, `stack: "zero"` base ──
        // (`validate_bar_layer_over_stacked_base`, `charts/extensibility_1`'s
        // `bullet` chart's own two-layer shape: a colorless/color-matched
        // bar-type layer, plus a disconnected `curve: "step"` goal-marker
        // line layer, both over the SAME grouped stacked base).

        /// `make_stacked_payload`'s own base: x `cat` (A/B), color `series`
        /// (x/y, alphabetical domain → `palette[0]`/`palette[1]`), y `val`,
        /// `stack: "zero"` — the one shape `validate_bar_layer_over_stacked_
        /// base` and the disconnected-step arm of `validate_line_layer` both
        /// admit. `layers` and `legend.visible` are the caller's own.
        fn bullet_like_payload(
            bar_layer: serde_json::Value,
            line_layer: serde_json::Value,
        ) -> serde_json::Value {
            let mut payload_json = make_stacked_payload("vertical", "zero");
            payload_json["rows"] = serde_json::json!([
                {"cat": "A", "series": "x", "val": 10.0, "overlay": 8.0, "goal": 9.0},
                {"cat": "A", "series": "y", "val": 30.0, "overlay": 8.0, "goal": 9.0},
                {"cat": "B", "series": "x", "val": 5.0, "overlay": 4.0, "goal": 6.0},
                {"cat": "B", "series": "y", "val": 15.0, "overlay": 4.0, "goal": 6.0},
            ]);
            // A 10-entry palette (`make_vertical_payload`'s own), not the
            // 2-entry stacked-test default — a colorless bar layer's own
            // "next unclaimed slot" (index 2 here) must land on a fill
            // distinct from the base's own two series colors to keep the
            // paint-order assertions below unambiguous.
            payload_json["resolved"]["palette"] = serde_json::json!([
                "#3164a3", "#779bc9", "#ad9c7f", "#7a8895", "#8a576f",
                "#5c7b5c", "#d49656", "#ae6349", "#609f9e", "#232f3a"
            ]);
            payload_json["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(true);
            payload_json["resolved"]["style"]["axis_y"]["grid"]["threshold"] =
                serde_json::json!({"color": "#8A8C8F", "width": 2.0});
            payload_json["resolved"]["layers"] = serde_json::json!([bar_layer, line_layer]);
            payload_json["resolved"]["legend"]["visible"] = serde_json::json!(true);
            payload_json
        }

        fn stacked_bar_layer_json(color: Option<&str>) -> serde_json::Value {
            // The base's two series hold slots 0 and 1: an ungrouped layer's ink is slot 2.
            let ink = if color.is_none() { serde_json::json!("#ad9c7f") } else { serde_json::Value::Null };
            serde_json::json!({
                "type": "bar",
                "bar_mark": {
                    "border": {"width": 1.0, "color": "#FFFFFF", "radius": 2.0},
                    "band_width": 0.8,
                    "labels": {
                        "visible": false, "position": null,
                        "field": null, "format": null, "dx": null, "dy": null, "font": null
                    }
                },
                "axis_y": {"position": null},
                "x": "cat",
                "y": "overlay",
                "label": null,
                "color": color,
                "ink": ink,
                "query_name": null
            })
        }

        fn disconnected_step_layer_json(stroke_cap: &str, disconnected_cap: Option<&str>) -> serde_json::Value {
            serde_json::json!({
                "type": "line",
                "line_mark": {
                    "stroke": {"width": 2.5, "color": "#dea628", "cap": stroke_cap, "join": "round"},
                    "halo_multiplier": 0.0,
                    "curve": "step",
                    "connect": false,
                    "disconnected_cap": disconnected_cap,
                    "disconnected_width": 0.9,
                    "labels": {"visible": false}
                },
                "point_mark": {
                    "size": null, "color": "#dea628", "filled": false, "fill": "#ffffff",
                    "stroke_width": 2.0, "labels": {"visible": false}
                },
                "axis_y": {"position": null},
                "x": null,
                "y": "goal",
                "label": "Goal",
                "color": null,
                "ink": "#dea628", "query_name": null
            })
        }

        /// An area layer's edge line carries no `disconnected_width`; a step
        /// layer that lacks one refuses with the line base's own wording.
        #[test]
        fn a_disconnected_step_layer_without_a_plateau_share_is_rejected_by_name() {
            let mut layer = disconnected_step_layer_json("round", Some("butt"));
            layer["line_mark"].as_object_mut().expect("an object").remove("disconnected_width");
            let payload_json = bullet_like_payload(stacked_bar_layer_json(None), layer);
            let err = render(&make_payload(payload_json), &test_fonts()).unwrap_err();
            assert!(err.contains("line_mark.disconnected_width must be resolved"), "{err}");
        }

        /// A colorless bar layer over a stacked, color-grouped base is admitted
        /// (`bullet`'s `Actual`); one labeled like a base series would merge two
        /// legend rows, so production raises `ERR_LAYER_LABEL_COLLISION`.
        #[test]
        fn a_colorless_bar_layer_labeled_like_a_stacked_base_series_is_rejected_by_name() {
            let render_with = |label: &str| {
                let mut bar = stacked_bar_layer_json(None);
                bar["label"] = serde_json::json!(label);
                let payload_json = bullet_like_payload(bar, disconnected_step_layer_json("round", Some("butt")));
                render(&make_payload(payload_json), &test_fonts())
            };
            render_with("Actual").expect("a distinct label renders");
            let err = render_with("x").unwrap_err();
            assert_eq!(err.code, "ERR-LAYER-LABEL-COLLISION", "{err}");
            assert_eq!(err.params["label"], "x");
        }

        /// Pins `docs/laws-layers.md`'s paint-order law byte-for-byte on
        /// `bullet`'s own two-layer shape: base bar, THEN every layer in
        /// authored order (here a bar-type layer, then the line),
        /// THEN the zero rule LAST — production's `[base_spec,
        /// *layer_specs]` (`_overlay.py:914`), the rule appended as a plain
        /// top-level sibling after every data layer. Regression: before the
        /// fix, the line layer painted BEFORE the bar-type
        /// layer loop — on `bullet`'s own real data (`actual > goal`) this
        /// buries the goal marker under the actual bar instead of drawing
        /// it on top.
        #[test]
        fn bar_type_layers_paint_before_the_trailing_layer_and_the_zero_rule_paints_last() {
            let payload_json = bullet_like_payload(
                stacked_bar_layer_json(None),
                disconnected_step_layer_json("round", Some("butt")),
            );
            let svg = render(&make_payload(payload_json), &test_fonts())
                .expect("a bar-type layer plus a trailing disconnected-step line layer render");
            // Legend swatches carry the same colors earlier in the document
            // (`fill="…" stroke="…"`), so match the marks' own attribute
            // shapes: the base rect's self-closing `fill="…"/>`, the layer
            // path's `Z" fill=`, the plateau's own `stroke-width`. A bare
            // `svg.find(color)` lands on the legend swatch and pins nothing.
            let base_i = svg.find("fill=\"#3164a3\"/>").expect("base bar mark paints");
            let bar_layer_i = svg
                .find("Z\" fill=\"#ad9c7f\"")
                .expect("the colorless bar-type layer's mark paints at the next palette slot");
            let line_i = svg
                .find("stroke=\"#dea628\" stroke-width=\"2.5\"")
                .expect("the trailing line layer's plateau paints");
            let rule_i = svg.rfind("stroke=\"#8A8C8F\"").expect("the zero rule paints");
            assert!(base_i < bar_layer_i, "base paints before the bar-type layer: {svg}");
            assert!(bar_layer_i < line_i, "the bar-type layer paints before the trailing line layer: {svg}");
            assert!(line_i < rule_i, "the trailing layer paints before the zero rule: {svg}");
        }

        /// `connect: false` swaps in `disconnected_cap` (theme default
        /// `"butt"`) for the plateau's own `stroke-linecap`, replacing the
        /// authored `stroke.cap` (`"round"` here) — the SAME law
        /// `common::resolve_stroke_cap` already ports for `line.rs`'s own
        /// band-step arm (`emitters/_layers.py`'s `line_mark.connect is
        /// False and line_mark.disconnected_cap is not None`). Regression:
        /// `OverlayLineMark` used to have no `disconnected_cap` field at
        /// all, so this deserialized away and the plateau kept the
        /// authored round cap, overhanging its own flush band edge.
        #[test]
        fn a_disconnected_step_layer_swaps_in_the_baked_disconnected_cap() {
            let payload_json = bullet_like_payload(
                stacked_bar_layer_json(None),
                disconnected_step_layer_json("round", Some("butt")),
            );
            let svg = render(&make_payload(payload_json), &test_fonts())
                .expect("a disconnected step-curve layer renders");
            assert!(
                svg.contains("stroke=\"#dea628\" stroke-width=\"2.5\" stroke-linecap=\"butt\""),
                "the plateau's cap is the baked disconnected_cap, not the authored round \
                 stroke.cap: {svg}"
            );
        }

        #[test]
        fn a_disconnected_step_layer_plateau_spans_the_authored_share_of_its_band() {
            let plateau_width = |share: f64| -> f64 {
                let mut layer = disconnected_step_layer_json("round", Some("butt"));
                layer["line_mark"]["disconnected_width"] = serde_json::json!(share);
                let svg = render(
                    &make_payload(bullet_like_payload(stacked_bar_layer_json(None), layer)),
                    &test_fonts(),
                )
                .expect("a disconnected step-curve layer renders");
                let marker = svg.find("fill=\"none\" stroke=\"#dea628\"").expect("layer plateau paints");
                let d_start = svg[..marker].rfind("d=\"").unwrap() + 3;
                let d = &svg[d_start..d_start + svg[d_start..].find('"').unwrap()];
                let xs: Vec<f64> = d
                    .split(['M', 'L'])
                    .filter(|pt| !pt.trim().is_empty())
                    .map(|pt| pt.trim().split(',').next().unwrap().parse().unwrap())
                    .collect();
                xs[1] - xs[0]
            };
            let ratio = plateau_width(0.5) / plateau_width(0.9);
            assert!((ratio - 0.5 / 0.9).abs() < 0.01, "{ratio}");
        }

        /// An absent `disconnected_cap` (never authored) falls back to the
        /// stroke's own cap — `common::resolve_stroke_cap`'s `unwrap_or`
        /// chain, mirrored from `line.rs`'s identical law.
        #[test]
        fn a_disconnected_step_layer_with_no_baked_disconnected_cap_falls_back_to_the_stroke_cap() {
            let payload_json = bullet_like_payload(
                stacked_bar_layer_json(None),
                disconnected_step_layer_json("square", None),
            );
            let svg = render(&make_payload(payload_json), &test_fonts())
                .expect("a disconnected step-curve layer with no disconnected_cap renders");
            assert!(
                svg.contains("stroke=\"#dea628\" stroke-width=\"2.5\" stroke-linecap=\"square\""),
                "the plateau falls back to the authored stroke.cap: {svg}"
            );
        }

        /// The color-matched arm reads the layer's OWN `bar_mark` — band
        /// width, border — never the base's, and paints the layer's own
        /// value labels (`_build_layer_label_specs` treats every
        /// `ResolvedBarLayer` alike, `is_stacked=False`, so the label sits
        /// at the plain per-row value position, not the stack's cumulative
        /// midpoint).
        #[test]
        fn a_color_matched_stacked_bar_layer_uses_its_own_mark_style_and_paints_its_own_labels() {
            let mut bar_layer = stacked_bar_layer_json(Some("series"));
            bar_layer["bar_mark"]["band_width"] = serde_json::json!(0.3);
            bar_layer["bar_mark"]["border"] =
                serde_json::json!({"width": 3.0, "color": "#111111", "radius": 5.0});
            bar_layer["bar_mark"]["labels"] = serde_json::json!({
                "visible": true, "position": "above",
                "field": null, "format": null, "dx": null, "dy": null, "font": null
            });
            let payload_json =
                bullet_like_payload(bar_layer, disconnected_step_layer_json("round", Some("butt")));
            let svg = render(&make_payload(payload_json), &test_fonts())
                .expect("a color-matched stacked bar layer renders");
            assert!(
                svg.contains("stroke=\"#111111\" stroke-width=\"3\""),
                "the layer's own border paints, not the base's: {svg}"
            );
            assert!(svg.contains("A 5.00,5.00"), "the layer's own corner radius paints: {svg}");
            assert!(svg.contains(">8<"), "the layer's own value label paints (row value 8): {svg}");
        }

        /// On a chart whose card fill is `transparent`, `canvas` is the one
        /// opaque ink: a bar layer's labels read it (`emitters/_overlay.py:
        /// 516,1992` hand `render_cartesian_overlay`'s `canvas` to
        /// `_build_bar_text_layer`), never `background`.
        const OVERLAY_CANVAS: &str = "#d9a45b";

        fn on_transparent_card(mut payload_json: serde_json::Value) -> serde_json::Value {
            payload_json["resolved"]["background"] = serde_json::json!("transparent");
            payload_json["resolved"]["canvas"] = serde_json::json!(OVERLAY_CANVAS);
            payload_json["background"] = serde_json::json!("#ffffff");
            payload_json
        }

        /// The `fill` of the `<{tag}` element containing `needle`.
        fn fill_of_tag(svg: &str, tag: &str, needle: &str) -> String {
            let i = svg.find(needle).unwrap_or_else(|| panic!("{needle} missing: {svg}"));
            let start = svg[..i + needle.len()].rfind(&format!("<{tag}")).expect("tag opens");
            let open = &svg[start..i + needle.len() + svg[i + needle.len()..].find('>').expect("tag end")];
            let at = open.find(" fill=\"").unwrap_or_else(|| panic!("no fill in {open}")) + 7;
            open[at..at + open[at..].find('"').expect("closing quote")].to_string()
        }

        #[test]
        fn a_color_matched_bar_layer_label_inks_the_canvas_not_the_transparent_card() {
            let mut bar_layer = stacked_bar_layer_json(Some("series"));
            bar_layer["bar_mark"]["labels"] = serde_json::json!({
                "visible": true, "position": "middle",
                "field": null, "format": null, "dx": null, "dy": null, "font": null
            });
            let payload_json = on_transparent_card(bullet_like_payload(
                bar_layer,
                disconnected_step_layer_json("round", Some("butt")),
            ));
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert_eq!(fill_of_tag(&svg, "text", ">8<"), OVERLAY_CANVAS, "{svg}");
        }

        #[test]
        fn a_colorless_bar_layer_label_inks_the_canvas_not_the_transparent_card() {
            let mut payload_json =
                make_vertical_payload(&[("1", 120.0), ("3", 150.0), ("5", 180.0)], false);
            payload_json["resolved"]["query_name"] = serde_json::json!("actual");
            payload_json["resolved"]["style"]["mark"]["labels"] = serde_json::json!({
                "visible": true, "position": "middle",
                "field": null, "format": null, "dx": null, "dy": null, "font": null
            });
            payload_json["resolved"]["layers"] = serde_json::json!([bar_layer_json(Some("projected"))]);
            payload_json["resolved"]["legend"]["visible"] = serde_json::json!(true);
            payload_json["datasets"] = serde_json::json!({
                "projected": [
                    {"product": "2", "target": 140.0},
                    {"product": "4", "target": 170.0},
                    {"product": "6", "target": 200.0}
                ]
            });
            let svg = render(&make_payload(on_transparent_card(payload_json)), &test_fonts())
                .expect("renders");
            assert_eq!(fill_of_tag(&svg, "text", ">140<"), OVERLAY_CANVAS, "{svg}");
        }

        /// A hollow overlay point authors no `fill` (`point_mark.fill` is
        /// `None` in the resolved model): the engine's default interior is
        /// `canvas`, never `background`.
        #[test]
        fn a_hollow_line_layer_point_knocks_out_with_the_canvas() {
            let mut layer = line_layer_json("#dea628");
            layer["point_mark"]["fill"] = serde_json::Value::Null;
            let payload_json =
                on_transparent_card(payload_with_layer(&[("Jan", 100.0, 90.0), ("Feb", 150.0, 140.0)], layer));
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert_eq!(fill_of_tag(&svg, "circle", "<circle transform"), OVERLAY_CANVAS, "{svg}");
        }

        /// A layer grouping by a color field of its own over a stacked base shares the
        /// scale but claims its own values after the base's; rows sharing a category and
        /// value stack in row order.
        #[test]
        fn a_bar_layer_with_a_color_of_its_own_over_a_stacked_base_claims_its_values_after_the_bases() {
            let mut bar_layer = stacked_bar_layer_json(Some("region"));
            bar_layer["query_name"] = serde_json::json!("layer_query");
            bar_layer["series_inks"] = serde_json::json!({"East": "#ad9c7f", "West": "#7a8895"});
            let mut payload_json = bullet_like_payload(bar_layer.clone(), disconnected_step_layer_json("round", None));
            payload_json["resolved"]["layers"] = serde_json::json!([bar_layer]);
            payload_json["datasets"] = serde_json::json!({
                "layer_query": [
                    {"cat": "A", "region": "East", "overlay": 4.0},
                    {"cat": "A", "region": "East", "overlay": 3.0},
                    {"cat": "B", "region": "West", "overlay": 4.0},
                ]
            });
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert_eq!(svg.matches("fill=\"#ad9c7f\" stroke=\"#FFFFFF\"").count(), 2, "East takes palette[2], one segment per row: {svg}");
            assert!(svg.contains("fill=\"#7a8895\" stroke=\"#FFFFFF\""), "West takes palette[3]: {svg}");
            assert_eq!(svg.matches(">East</text>").count(), 1, "one legend entry per value: {svg}");
        }

        /// A layer row's series value outside the base's own color domain joins the
        /// shared scale after the base's values: it takes the next palette slot, the
        /// legend lists it, and its segments stack below the base's series.
        #[test]
        fn a_color_matched_stacked_bar_layer_row_outside_the_base_color_domain_takes_the_next_slot() {
            let mut bar_layer = stacked_bar_layer_json(Some("series"));
            bar_layer["query_name"] = serde_json::json!("layer_query");
            bar_layer["series_inks"] = serde_json::json!({"z": "#ad9c7f"});
            let mut payload_json =
                bullet_like_payload(bar_layer, disconnected_step_layer_json("round", Some("butt")));
            payload_json["datasets"] = serde_json::json!({
                "layer_query": [
                    {"cat": "A", "series": "z", "overlay": 8.0},
                    {"cat": "B", "series": "z", "overlay": 4.0},
                ]
            });
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert_eq!(svg.matches(">z</text>").count(), 1, "one legend entry for the new series: {svg}");
            assert_eq!(svg.matches("fill=\"#ad9c7f\" stroke=\"#FFFFFF\"").count(), 2, "z takes the third palette slot: {svg}");
        }
    }

    /// `make_vertical_payload(_, false)` is single-series (no `color:`) — a
    /// per_series entry needs the chart's own color-domain series list, so
    /// this refuses via `visual_row_count`'s `series_count > 0` guard before
    /// any strip row is built, on every arm (`composed_cfg`, rail-concat,
    /// and — the one this single-series fixture actually reaches — the
    /// legacy splice).
    #[test]
    fn attached_support_table_with_per_series_entry_and_no_color_field_is_rejected() {
        let mut payload_json = make_vertical_payload(&[("Alpha", 1000.0)], false);
        payload_json["resolved"]["support_table"] =
            serde_json::json!({"entries": [{"per_series": "revenue", "format": ",d"}]});
        payload_json["resolved"]["effective_support_table_style"] = support_table_style_json();
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let err = render(&payload, &fonts).unwrap_err();
        assert!(err.contains("per_series"), "error names the unsupported shape: {err}");
        assert!(err.contains("series_count"), "names the missing input: {err}");
    }

    /// Payload for the QUANTITATIVE-x arm: numeric x values, right y axis
    /// with a baked ladder, an authored x label format — the compiled shape
    /// `continuous_bar_size_prop` sizes. Mirrors the
    /// fixtures/continuous-x-bar-width corpus group (mark gap 3 / min 4 /
    /// max 20, scale padding 0 so the half-bar reservation drives the pad).
    fn make_continuous_payload(xs: &[(f64, f64)]) -> serde_json::Value {
        use serde_json::json;
        let mut payload_json = make_vertical_payload(&[("Alpha", 1000.0)], false);
        let rows: Vec<_> =
            xs.iter().map(|(x, y)| json!({"product": x, "revenue": y})).collect();
        payload_json["rows"] = json!(rows);
        payload_json["resolved"]["style"]["axis_x"]["labels"]["format"] = json!(".3~s");
        payload_json["resolved"]["style"]["axis_x"]["ticks"]["visible"] = json!(false);
        payload_json["resolved"]["style"]["axis_y"]["position"] = json!("right");
        payload_json["resolved"]["style"]["axis_y"]["ticks"]["visible"] = json!(true);
        payload_json["resolved"]["style"]["axis_y"]["tick_values"] =
            json!([0.0, 100.0, 200.0, 300.0]);
        payload_json["resolved"]["style"]["mark"]["size"] = json!(null);
        payload_json["resolved"]["style"]["mark"]["gap"] = json!(3.0);
        payload_json["resolved"]["style"]["mark"]["min_size"] = json!(4.0);
        payload_json["resolved"]["style"]["mark"]["max_size"] = json!(20.0);
        payload_json["width"] = json!(600.0);
        payload_json["height"] = json!(432.0);
        payload_json
    }

    fn bar_widths(svg: &str) -> Vec<f64> {
        // bar_passes paths: "M x,y2 V .. A r,.. H x2−r A .. V y2 Z" —
        // width = (H value + r) − x, with r the corner radius (2 in the
        // fixture theme). Falls back to <rect> widths for square bars.
        let mut out: Vec<f64> = svg
            .split("d=\"M ")
            .skip(1)
            .filter_map(|seg| {
                if !seg.split("/>").next()?.contains("fill=\"#3164a3\"") {
                    return None;
                }
                let x: f64 = seg.split(',').next()?.parse().ok()?;
                let h: f64 = seg.split(" H ").nth(1)?.split(' ').next()?.parse().ok()?;
                let r: f64 = seg.split(" A ").nth(1)?.split(',').next()?.parse().ok()?;
                Some(h + r - x)
            })
            .collect();
        out.extend(
            svg.split("<rect ")
                .skip(1)
                .filter_map(|seg| {
                    let tag = seg.split("/>").next()?;
                    let w: f64 =
                        tag.split("width=\"").nth(1)?.split('"').next()?.parse().ok()?;
                    tag.contains("fill=\"#3164a3\"").then_some(w)
                }),
        );
        out
    }

    /// Uneven years: three bands would space the first two bars as many steps
    /// as the last two, eleven year bands put the third bar ten steps from the first.
    #[test]
    fn a_sparse_year_column_is_gap_filled_into_year_bands() {
        use serde_json::json;
        let mut p = make_vertical_payload(&[("2010", 1.0), ("2011", 2.0), ("2020", 3.0)], false);
        p["resolved"]["style"]["mark"]["border"]["radius"] = json!(0.0);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let xs: Vec<f64> = svg
            .match_indices("<rect x=\"")
            .map(|(i, _)| svg[i + 9..].split('"').next().unwrap().parse().unwrap())
            .collect();
        let step = xs[1] - xs[0];
        assert!(((xs[2] - xs[0]) / step - 10.0).abs() < 0.05, "eleven year bands, the third bar ten steps out: {xs:?}");
    }

    /// Integer years are rewritten to dates before the x type is decided, so
    /// they take the banded arm; an authored `axis_x.type: ordinal` keeps
    /// numeric x banded too.
    #[test]
    fn year_shaped_and_authored_ordinal_x_stay_banded() {
        let linear = |xs: &[(f64, f64)], edit: &dyn Fn(&mut serde_json::Value)| {
            let mut p = make_continuous_payload(xs);
            edit(&mut p);
            let payload = make_payload(p);
            let r = BarResolved::from_payload(&payload).unwrap();
            let layers = XLayers { all: &[], payload: &payload };
            render_vertical_continuous_x(&payload, &test_fonts(), &r, &layers).unwrap().is_some()
        };
        let hours: Vec<(f64, f64)> = (0..24).map(|h| (h as f64, 100.0 + h as f64)).collect();
        assert!(linear(&hours, &|_| {}), "numeric x is linear");
        assert!(
            !linear(&hours, &|p| p["resolved"]["style"]["axis_x"]["type"] = serde_json::json!("ordinal")),
            "an authored ordinal type keeps numeric x banded"
        );
        let years: Vec<(f64, f64)> = (2017..=2024).map(|y| (f64::from(y), 100.0 + f64::from(y))).collect();
        assert!(!linear(&years, &|_| {}), "a year column is a date, so it is banded");
    }

    /// The quantitative-x gate: numeric x routes off the band arm — the
    /// x axis shows d3 ticks over the padded-nice linear domain (0/5/10/…),
    /// never one band label per value — and the computed width is the
    /// clamp ladder's, verified against the continuous-x-bar-width corpus
    /// oracle (24 hourly points on a 600px card: step ≈ 20.8px − gap 3,
    /// under the 20px ceiling).
    #[test]
    fn continuous_x_routes_to_the_linear_arm_with_the_clamp_width() {
        let xs: Vec<(f64, f64)> = (0..24).map(|h| (h as f64, 100.0 + h as f64)).collect();
        let payload = make_payload(make_continuous_payload(&xs));
        let svg = render(&payload, &test_fonts()).expect("continuous-x renders");
        assert!(svg.contains(">5</text>"), "d3 tick labels, not band labels:\n{svg}");
        assert!(!svg.contains(">23</text>"), "no per-value band label:\n{svg}");
        let ws = bar_widths(&svg);
        assert_eq!(ws.len(), 24, "one path per bar (single stroke pass): {ws:?}");
        let w = ws[0];
        assert!((15.0..20.0).contains(&w), "clamp ladder width (step−gap<max): {w}");
        // Path coordinates emit at 2 decimals, so reconstructed widths
        // wobble ±0.01 around the one computed clamp value.
        assert!(ws.iter().all(|x| (x - w).abs() < 0.02), "uniform width: {ws:?}");
    }

    /// `render_vertical_continuous_x`'s own `r.background.as_str()` read
    /// (see `BarResolved::background`) feeds the canvas rect independently
    /// of the band arm's own site.
    #[test]
    fn continuous_x_canvas_rect_reads_the_chart_own_background_not_the_board_background() {
        let xs: Vec<(f64, f64)> = (0..24).map(|h| (h as f64, 100.0 + h as f64)).collect();
        let mut payload_json = make_continuous_payload(&xs);
        payload_json["resolved"]["background"] = serde_json::json!("#d9a45b");
        payload_json["background"] = serde_json::json!("#ffffff");
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("continuous-x renders");
        assert!(svg.contains("<rect width=\"600.0\" height=\"432.0\" fill=\"#d9a45b\"/>"), "{svg}");
    }

    /// An authored `mark.size` bypasses the ladder as a literal width.
    #[test]
    fn continuous_x_authored_size_is_literal() {
        let xs: Vec<(f64, f64)> = (0..24).map(|h| (h as f64, 100.0 + h as f64)).collect();
        let mut payload_json = make_continuous_payload(&xs);
        payload_json["resolved"]["style"]["mark"]["size"] = serde_json::json!(6.0);
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("authored size renders");
        let ws = bar_widths(&svg);
        assert!(!ws.is_empty() && ws.iter().all(|w| (w - 6.0).abs() < 1e-6), "{ws:?}");
    }

    fn make_time_payload(dates: &[String]) -> serde_json::Value {
        let xs: Vec<(f64, f64)> = dates.iter().enumerate().map(|(i, _)| (0.0, 100.0 + i as f64)).collect();
        let mut payload_json = make_continuous_payload(&xs);
        let rows: Vec<_> = dates
            .iter()
            .enumerate()
            .map(|(i, d)| serde_json::json!({"product": d, "revenue": 100.0 + i as f64}))
            .collect();
        payload_json["rows"] = serde_json::json!(rows);
        payload_json["resolved"]["style"]["axis_x"]["labels"]["format"] = serde_json::json!(null);
        payload_json["resolved"]["style"]["axis_x"]["labels"]["overlap"] = serde_json::json!({"skip": true, "tilt": true});
        payload_json["resolved"]["style"]["axis_x"]["ticks"] =
            serde_json::json!({"color": "#DFE1E5", "visible": false, "count": null});
        payload_json["resolved"]["style"]["label_usable_ratio"] = serde_json::json!(0.8);
        payload_json["resolved"]["style"]["axis_x"]["fill"] = serde_json::json!("null");
        payload_json
    }

    /// A century of yearly bars steps its labels to every 10th year: 1901 lies 9 of 10 years before the 1910 tick, but
    /// its label would leave less than `EDGE_LABEL_MIN_GAP_RATIO` of the tick spacing empty beside 1910's, so it stays unnamed.
    #[test]
    fn a_stepped_year_bar_axis_leaves_a_first_point_crowding_its_neighbor_unlabeled() {
        let dates: Vec<String> = (0..100).map(|i| format!("{}-01-01", 1901 + i)).collect();
        let svg = render(&make_payload(make_time_payload(&dates)), &test_fonts()).expect("renders");
        assert_eq!(svg_year_labels(&svg), [1910, 1920, 1930, 1940, 1950, 1960, 1970, 1980, 1990, 2000]);
    }

    /// A stepped year axis names a year that no tick names (1991 before the 10-year ticks from 2000), so the first
    /// point keeps its label wherever the gap rule leaves room.
    #[test]
    fn a_stepped_year_bar_axis_labels_a_first_point_no_tick_names() {
        let dates: Vec<String> = (0..90).map(|i| format!("{}-01-01", 1991 + i)).collect();
        let mut payload = make_payload(make_time_payload(&dates));
        payload.width = 900.0;
        let years = svg_year_labels(&render(&payload, &test_fonts()).expect("renders"));
        assert_eq!(years[..3], [1991, 2000, 2010], "{years:?}");
    }

    fn months_from_2000(n: usize) -> Vec<String> {
        (0..n).map(|i| format!("{}-{:02}-01", 2000 + i / 12, i % 12 + 1)).collect()
    }

    /// Left edge and width of every bar: a rounded path, or the plain rect a stack's lower segment is.
    fn bar_extents(svg: &str) -> Vec<(f64, f64)> {
        let paths = svg.split("d=\"M ").skip(1).filter_map(|seg| {
            if !seg.split("/>").next()?.contains("fill=\"#3164a3\"") {
                return None;
            }
            let x: f64 = seg.split(',').next()?.parse().ok()?;
            let h: f64 = seg.split(" H ").nth(1)?.split(' ').next()?.parse().ok()?;
            let r: f64 = seg.split(" A ").nth(1)?.split(',').next()?.parse().ok()?;
            Some((x, h + r - x))
        });
        let rects = svg.split("<rect x=\"").skip(1).filter_map(|seg| {
            let tag = seg.split("/>").next()?;
            // A legend swatch strokes at 1.5.
            if !tag.contains("fill=\"#3164a3\"") || tag.contains("stroke-width=\"1.5\"") {
                return None;
            }
            let x: f64 = tag.split('"').next()?.parse().ok()?;
            let w: f64 = tag.split("width=\"").nth(1)?.split('"').next()?.parse().ok()?;
            Some((x, w))
        });
        paths.chain(rects).collect()
    }

    /// A monthly column past the ordinal budget keeps its calendar grain on a UTC
    /// time axis: each bar spans its own bucket, so a 31-day month draws wider
    /// than a 29-day one, and the thinned year labels sit on drawn ticks even
    /// though the theme hides the axis's ticks.
    #[test]
    fn a_bucketed_time_axis_spans_each_bucket_and_draws_its_opener_ticks() {
        let _locale = crate::time_locale::en_us_scope();
        let svg = render(&make_payload(make_time_payload(&months_from_2000(72))), &test_fonts()).expect("renders");
        let ext = bar_extents(&svg);
        assert_eq!(ext.len(), 72);
        // `0.8 * span - 1` is a bar's width, its span the bucket's pixel width.
        let span = |i: usize| (ext[i].1 + 1.0) / 0.8;
        assert!((span(0) / span(1) - 31.0 / 29.0).abs() < 0.01, "Jan 2000 over Feb 2000: {}", span(0) / span(1));
        assert!(svg.contains(">2005</text>") && !svg.contains(">Jul</text>"), "year labels only");
        let ticks = tick_xs(&svg).len();
        assert_eq!(ticks, 7, "the six labeled years' boundaries, closed at the axis end");
    }

    /// The x of the `<text>` painting `label`, and of every vertical tick rule.
    fn label_x(svg: &str, label: &str) -> f64 {
        let at = svg.find(&format!(">{label}</text>")).unwrap_or_else(|| panic!("no {label} label"));
        svg[..at].rsplit("<text x=\"").next().unwrap().split('"').next().unwrap().parse().unwrap()
    }

    fn tick_xs(svg: &str) -> Vec<f64> {
        let rules = svg.split("<line x1=\"").skip(1).filter(|l| l.contains("stroke-width=\"1\"/>"));
        rules
            .filter_map(|l| {
                let x1: f64 = l.split('"').next()?.parse().ok()?;
                let x2: f64 = l.split("x2=\"").nth(1)?.split('"').next()?.parse().ok()?;
                (x1 == x2).then_some(x1)
            })
            .collect()
    }

    /// Year labels over monthly bars on a UTC time axis sit between their boundary ticks, not on a tick.
    #[test]
    fn a_time_axis_centers_year_labels_between_their_boundary_ticks() {
        let _locale = crate::time_locale::en_us_scope();
        let svg = render(&make_payload(make_time_payload(&months_from_2000(72))), &test_fonts()).expect("renders");
        let ticks = tick_xs(&svg);
        assert_eq!(ticks.len(), 7, "six years framed by seven boundaries: {ticks:?}");
        for (k, year) in ["2000", "2002", "2004"].into_iter().enumerate() {
            let i = 2 * k;
            assert!((label_x(&svg, year) - (ticks[i] + ticks[i + 1]) / 2.0).abs() < 1.0, "{year}: {ticks:?}");
        }
    }

    /// Twenty-four years of monthly bars on a 300px card cannot name every year between its ticks: the years thin to
    /// every fifth, on their ticks.
    #[test]
    fn a_long_time_axis_thins_year_labels_on_their_ticks() {
        let _locale = crate::time_locale::en_us_scope();
        let mut p = make_time_payload(&months_from_2000(288));
        p["width"] = serde_json::json!(300.0);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let ticks = tick_xs(&svg);
        for year in ["2000", "2005", "2010", "2015", "2020"] {
            let at = label_x(&svg, year);
            assert!(ticks.iter().any(|t| (t - at).abs() < 1.0), "{year} at {at} is off the ticks {ticks:?}");
        }
        assert!(!svg.contains(">2001</text>"), "an unthinned year label");
    }

    /// Interval boundary ticks sit inside the plot, and the floor rule reaches under the outermost one.
    #[test]
    fn interval_boundary_ticks_sit_inside_the_plot_under_the_floor_rule() {
        let _locale = crate::time_locale::en_us_scope();
        let svg = render(&make_payload(make_time_payload(&months_from_2000(72))), &test_fonts()).expect("renders");
        let plot_left: f64 = svg.split("<g transform=\"translate(").nth(1).unwrap().split(',').next().unwrap().parse().unwrap();
        let ticks: Vec<f64> = tick_xs(&svg).into_iter().map(|x| x - plot_left).collect();
        let rule = svg
            .split("<line ")
            .find(|l| l.contains("stroke-width=\"2\""))
            .expect("a floor rule");
        let attr = |name: &str| -> f64 { rule.split(&format!("{name}=\"")).nth(1).unwrap().split('"').next().unwrap().parse().unwrap() };
        let (first, last) = (ticks[0], *ticks.last().unwrap());
        assert!(first >= 0.5, "the first boundary lands inside the plot: {ticks:?}");
        assert!(attr("x1") <= first - 0.5 && attr("x2") >= last + 0.5, "rule {}..{} vs ticks {first}..{last}", attr("x1"), attr("x2"));
    }

    /// Monthly bars under quarterly labels: the label centers on its quarter's middle bar and the ticks fall in the
    /// gap between quarters (positions relative to the bar step, the axis and the bars paint in different frames).
    #[test]
    fn a_band_axis_centers_quarter_labels_under_their_bars_with_ticks_in_the_gaps() {
        let _locale = crate::time_locale::en_us_scope();
        let mut p = make_time_payload(&months_from_2000(24));
        p["resolved"]["style"]["axis_x"]["labels"]["time_unit"] = serde_json::json!("yearquarter");
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let step = bar_extents(&svg)[1].0 - bar_extents(&svg)[0].0;
        let ticks = tick_xs(&svg);
        assert_eq!(ticks.len(), 9, "eight quarters framed by nine boundaries: {ticks:?}");
        let q1 = label_x(&svg, "Q1");
        assert!((ticks[0] + 1.5 * step - q1).abs() < 1.0, "Q1 centered on its middle bar: {q1} {ticks:?}");
        assert!((ticks[1] - ticks[0] - 3.0 * step).abs() < 1.0, "one tick per quarter gap: {ticks:?}");
    }

    /// An authored `labels.align` keeps interval labels on their ticks rather than centering them on the period.
    #[test]
    fn an_authored_label_align_keeps_band_quarter_labels_on_the_ticks() {
        let _locale = crate::time_locale::en_us_scope();
        let mut p = make_time_payload(&months_from_2000(24));
        p["resolved"]["style"]["axis_x"]["labels"]["time_unit"] = serde_json::json!("yearquarter");
        p["resolved"]["style"]["axis_x"]["labels"]["align"] = serde_json::json!("left");
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let ticks = tick_xs(&svg);
        assert!(!ticks.is_empty(), "no ticks: {svg}");
        let q1 = label_x(&svg, "Q1");
        assert!(ticks.iter().any(|t| (t - q1).abs() < 1.0), "Q1 at {q1} is off the ticks {ticks:?}");
    }

    /// An authored tick cadence keeps labels on their ticks for every family, bars included.
    #[test]
    fn an_authored_tick_count_keeps_band_quarter_labels_on_the_ticks() {
        let _locale = crate::time_locale::en_us_scope();
        let mut p = make_time_payload(&months_from_2000(24));
        p["resolved"]["style"]["axis_x"]["labels"]["time_unit"] = serde_json::json!("yearquarter");
        p["resolved"]["style"]["axis_x"]["ticks"]["count"] = serde_json::json!(8.0);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let ticks = tick_xs(&svg);
        assert!(!ticks.is_empty(), "no ticks: {svg}");
        let q1 = label_x(&svg, "Q1");
        assert!(ticks.iter().any(|t| (t - q1).abs() < 1.0), "Q1 at {q1} is off the ticks {ticks:?}");
    }

    /// `labelOverlap: parity` thins the on-tick labels by index; a bottom strip asks for it, but interval labels are
    /// never thinned, so none is hidden.
    #[test]
    fn interval_labels_over_a_bottom_strip_are_never_parity_hidden() {
        let _locale = crate::time_locale::en_us_scope();
        let mut p = make_time_payload(&months_from_2000(48));
        p["width"] = serde_json::json!(100.0);
        p["resolved"]["style"]["axis_x"]["labels"]["tilt_increments"] = serde_json::json!([0.0]);
        p["resolved"]["style"]["axis_x"]["labels"]["time_unit"] = serde_json::json!("yearquarter");
        p["resolved"]["support_table"] = serde_json::json!({
            "entries": [{"source": "revenue", "format": {"spec": "$,.0f", "raw": "currency_whole"}, "label": "Revenue"}]
        });
        p["resolved"]["effective_support_table_style"] = support_table_style_json_bottom();
        p["resolved"]["support_table_axis_offset"] = serde_json::json!(17.0);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(!svg.contains("opacity=\"0\""), "an interval label was parity-hidden");
    }

    /// An authored label format names the period its text reads (a month here), not the cadence's: the labels stay on
    /// their bars rather than centering on a coarser period.
    #[test]
    fn an_authored_month_format_keeps_its_labels_on_their_bars() {
        let _locale = crate::time_locale::en_us_scope();
        let mut p = make_time_payload(&months_from_2000(36));
        p["width"] = serde_json::json!(300.0);
        p["resolved"]["style"]["axis_x"]["labels"]["format"] = serde_json::json!("%b %Y");
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let at = label_x(&svg, "Jan 2000");
        assert!(tick_xs(&svg).iter().any(|t| (t - at).abs() < 1.0), "no tick under the label: {at} {:?}", tick_xs(&svg));
    }

    /// Label period equal to the data grain stays on its own bar.
    #[test]
    fn a_band_axis_keeps_equal_grain_labels_on_their_bars() {
        let _locale = crate::time_locale::en_us_scope();
        let svg = render(&make_payload(make_time_payload(&months_from_2000(12))), &test_fonts()).expect("renders");
        let step = bar_extents(&svg)[1].0 - bar_extents(&svg)[0].0;
        assert!((label_x(&svg, "Mar") - label_x(&svg, "Jan") - 2.0 * step).abs() < 1.0);
        assert!(tick_xs(&svg).is_empty(), "no restored ticks");
    }

    /// A time-axis bar payload over `(date, revenue)` rows, in the order given.
    fn dated_bars(rows: &[(&str, f64)]) -> Payload {
        let mut json = make_time_payload(&rows.iter().map(|(d, _)| d.to_string()).collect::<Vec<_>>());
        json["rows"] = serde_json::Value::Array(rows.iter().map(|(d, v)| serde_json::json!({"product": d, "revenue": v})).collect());
        make_payload(json)
    }

    /// Rows the query returned out of order draw the bars the ordered rows do.
    #[test]
    fn unordered_rows_draw_the_bars_of_ordered_rows() {
        let _locale = crate::time_locale::en_us_scope();
        let ordered = [("2024-03-01T08:00:00", 3.0), ("2024-03-02T09:30:00", 5.0), ("2024-03-04T07:15:00", 4.0), ("2024-03-09T12:00:00", 9.0)];
        let shuffled = [ordered[2], ordered[0], ordered[3], ordered[1]];
        let draw = |rows: &[(&str, f64)]| render(&dated_bars(rows), &test_fonts()).expect("renders");
        assert_eq!(crate::time_axis::testing::geometry(&draw(&ordered)), crate::time_axis::testing::geometry(&draw(&shuffled)));
    }

    /// Two spellings of one instant seat in the order their text sorts, whatever order the rows come in.
    #[test]
    fn spellings_of_one_instant_seat_the_same_in_any_row_order() {
        let _locale = crate::time_locale::en_us_scope();
        let ordered = [("2024-03-01", 3.0), ("2024-03-01T00:00:00Z", 5.0), ("2024-03-05T12:00:00", 4.0), ("2024-03-09", 9.0)];
        let shuffled = [ordered[1], ordered[3], ordered[0], ordered[2]];
        let draw = |rows: &[(&str, f64)]| render(&dated_bars(rows), &test_fonts()).expect("renders");
        assert_eq!(crate::time_axis::testing::geometry(&draw(&ordered)), crate::time_axis::testing::geometry(&draw(&shuffled)));
    }

    /// An offset timestamp sits where its UTC spelling does.
    #[test]
    fn an_offset_timestamp_bar_sits_where_its_utc_spelling_does() {
        let _locale = crate::time_locale::en_us_scope();
        let offset = [("2024-03-01T23:30:00-05:00", 3.0), ("2024-03-03T01:00:00+05:30", 5.0), ("2024-03-04T08:00:00+0100", 4.0)];
        let utc = [("2024-03-02T04:30:00Z", 3.0), ("2024-03-02T19:30:00Z", 5.0), ("2024-03-04T07:00:00Z", 4.0)];
        let draw = |rows: &[(&str, f64)]| render(&dated_bars(rows), &test_fonts()).expect("renders");
        assert_eq!(crate::time_axis::testing::geometry(&draw(&offset)), crate::time_axis::testing::geometry(&draw(&utc)));
    }

    /// A time x value that is no date refuses by the code every x-axis family raises.
    #[test]
    fn a_time_x_value_that_is_no_date_is_refused_by_name() {
        let rows = [("2024-03-01T08:00:00", 3.0), ("nope", 5.0), ("2024-03-04T07:15:00", 4.0), ("2024-03-09T12:00:00", 9.0)];
        let mut json = make_time_payload(&rows.iter().map(|(d, _)| d.to_string()).collect::<Vec<_>>());
        json["rows"] = serde_json::Value::Array(rows.iter().map(|(d, v)| serde_json::json!({"product": d, "revenue": v})).collect());
        json["resolved"]["style"]["axis_x"]["type"] = serde_json::json!("temporal");
        let err = render(&make_payload(json), &test_fonts()).expect_err("an unparseable time x refuses");
        let text = err.to_string();
        assert!(text.contains("ERR-X-VALUE-NOT-A-DATE"), "{text}");
        for param in ["x_field=\"product\"", "value=\"nope\""] {
            assert!(text.contains(param), "{param}: {text}");
        }
    }

    /// An authored x domain may end on a timestamp, which the UTC scale takes as the instant it is.
    #[test]
    fn an_authored_timestamp_domain_sets_the_time_scale() {
        let _locale = crate::time_locale::en_us_scope();
        let mut json = make_time_payload(&months_from_2000(72));
        json["resolved"]["style"]["axis_x"]["scale"]["continuous"]["domain"] = serde_json::json!(["2000-01-01T12:00:00", "2006-01-01T12:00:00"]);
        render(&make_payload(json), &test_fonts()).expect("renders");
    }

    /// A band x whose grain detection tolerated a value that is no date keeps it as a category.
    #[test]
    fn a_band_x_keeps_a_value_that_is_no_date_as_a_category() {
        let _locale = crate::time_locale::en_us_scope();
        let mut cats: Vec<(String, f64)> = (1..=12).map(|m| (format!("2024-{m:02}-01"), 10.0 + f64::from(m))).collect();
        cats.push(("Unknown".to_string(), 5.0));
        let refs: Vec<(&str, f64)> = cats.iter().map(|(c, v)| (c.as_str(), *v)).collect();
        let mut json = make_vertical_payload(&refs, false);
        json["resolved"]["style"]["label_usable_ratio"] = serde_json::json!(0.8);
        json["resolved"]["style"]["axis_x"]["fill"] = serde_json::json!("null");
        render(&make_payload(json), &test_fonts()).expect("a band keeps the value as a category");
    }

    /// Two series per month on the time axis: `stack` is `"none"` (grouped) or `"zero"`.
    fn make_time_series_payload(stack: &str) -> serde_json::Value {
        let mut p = make_time_payload(&months_from_2000(72));
        p["resolved"]["color"] = serde_json::json!("series");
        p["resolved"]["stack"] = serde_json::json!(stack);
        p["resolved"]["palette"] = serde_json::json!(["#3164a3", "#779bc9"]);
        let rows: Vec<_> = months_from_2000(72)
            .iter()
            .flat_map(|d| {
                [("x", 10.0), ("y", 30.0)].map(|(series, v)| serde_json::json!({"product": d, "series": series, "revenue": v}))
            })
            .collect();
        p["rows"] = serde_json::json!(rows);
        p
    }

    /// Fill colors of a rendered SVG's bar paths, in paint order.
    fn bar_fills(svg: &str) -> Vec<&str> {
        svg.split("d=\"M ").skip(1).filter_map(|seg| seg.split("/>").next()?.split("fill=\"").nth(1)?.split('"').next()).collect()
    }

    /// Grouped series split a bucket in n sub-bars on Vega-Lite's offset step: 0.8 of the canonical month
    /// (31 days) over n, whatever the bucket's own length.
    #[test]
    fn grouped_series_on_a_time_axis_share_one_offset_step() {
        let _locale = crate::time_locale::en_us_scope();
        let svg = render(&make_payload(make_time_series_payload("none")), &test_fonts()).expect("renders");
        let fills = bar_fills(&svg);
        assert_eq!(fills.len(), 144);
        assert!(fills.contains(&"#3164a3") && fills.contains(&"#779bc9"), "{fills:?}");
        let ext: Vec<(f64, f64)> = bar_extents(&svg);
        assert_eq!(ext.len(), 72, "the first series' bars");
        let w: Vec<f64> = ext.iter().map(|e| e.1).collect();
        assert!(w.iter().all(|x| (x - w[0]).abs() < 0.011), "one width for every sub-bar: {w:?}");
    }

    /// A stacked series piles in one bar per bucket, so every sub-bar shares the unsplit bucket width.
    #[test]
    fn stacked_series_on_a_time_axis_share_the_bucket_bar() {
        let _locale = crate::time_locale::en_us_scope();
        let svg = render(&make_payload(make_time_series_payload("zero")), &test_fonts()).expect("renders");
        let ext = bar_extents(&svg);
        assert_eq!(ext.len(), 72);
        let span = |i: usize| (ext[i].1 + 1.0) / 0.8;
        assert!((span(0) / span(1) - 31.0 / 29.0).abs() < 0.01, "Jan 2000 over Feb 2000: {}", span(0) / span(1));
    }

    /// The outline a continuous-time stack strokes around its whole bar rounds at a quarter of the bar's
    /// width, not the authored radius.
    #[test]
    fn a_thin_time_stack_outline_rounds_to_a_quarter_of_its_width() {
        let _locale = crate::time_locale::en_us_scope();
        let mut p = make_time_series_payload("zero");
        p["resolved"]["style"]["mark"]["border"] = serde_json::json!({"width": 1.0, "color": "#FFFFFF", "radius": 2.0});
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let outline = svg.split("<path d=\"").skip(1).find(|seg| seg.contains("fill=\"none\"") && seg.split('"').next().unwrap().contains(" V ")).expect("an outline path");
        let radius = outline.split(" A ").nth(1).unwrap().split(',').next().unwrap();
        assert_eq!(radius, "1.07", "{outline}");
    }

    /// Grouped series under endpoint labels take the right rail a stack does, a value then a name
    /// (this fixture is flat, so it pins the shape, not which bar the value comes from); a stack's
    /// midpoint anchor is no series value, so it stays name-only.
    #[test]
    fn endpoint_labels_on_grouped_series_over_a_time_axis_draw_the_rail() {
        let _locale = crate::time_locale::en_us_scope();
        let rail = |stack: &str| {
            let mut p = make_time_series_payload(stack);
            p["resolved"]["style"]["endpoint_labels"] = serde_json::json!({"visible": true, "label_offset": 6.0, "height": 20.0});
            p["resolved"]["style"]["series_label"]["gap_px"] = serde_json::json!(8.0);
            render(&make_payload(p), &test_fonts()).expect("renders")
        };
        let grouped = rail("none");
        assert!(grouped.contains(">10 x</text>") && grouped.contains(">30 y</text>"), "{grouped}");
        let stacked = rail("zero");
        assert!(stacked.contains(">x</text>") && stacked.contains(">y</text>"), "{stacked}");
    }

    fn with_value_labels(mut p: serde_json::Value) -> serde_json::Value {
        p["resolved"]["style"]["mark"]["labels"] = serde_json::json!({
            "visible": true, "field": null, "format": ",.0f", "dx": null, "dy": null, "font": null, "position": null
        });
        p["resolved"]["style"]["axis_x"]["scale"] = serde_json::json!({"padding": 0.0});
        p
    }

    /// Value labels beside a `timeFormat` expression speak the format at every tick, on the point scale's categories.
    #[test]
    fn value_labels_beside_a_time_format_expr_speak_it_at_every_category_tick() {
        let _locale = crate::time_locale::en_us_scope();
        let mut p = with_value_labels(make_time_payload(&months_from_2000(72)));
        p["resolved"]["style"]["axis_x"]["labels"]["expr"] = serde_json::json!("timeFormat(datum.value, '%m/%y')");
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(svg.contains(">01/05</text>"), "{svg}");
    }

    /// A date axis under value labels needs the theme's scale padding: an absent one is a payload fault.
    #[test]
    fn value_labels_on_a_time_axis_require_the_scale_padding() {
        let mut p = with_value_labels(make_time_payload(&months_from_2000(72)));
        p["resolved"]["style"]["axis_x"]["scale"] = serde_json::json!({});
        let err = render(&make_payload(p), &test_fonts()).expect_err("refuses").to_string();
        assert!(err.contains("axis_x.scale.padding absent"), "{err}");
    }

    /// Value labels type the field nominal beside the bars: the dates become the categories of a point scale,
    /// evenly spaced whatever the days between them, and each bar is Vega-Lite's 18px step bar centered on its date.
    #[test]
    fn value_labels_put_a_time_axis_on_a_point_scale_of_18px_bars() {
        let svg = render(&make_payload(with_value_labels(make_time_payload(&months_from_2000(72)))), &test_fonts()).expect("renders");
        let ext = bar_extents(&svg);
        assert_eq!(ext.len(), 72);
        assert!(ext.iter().all(|&(_, w)| (w - 18.0).abs() < 0.011), "{ext:?}");
        let center = |i: usize| ext[i].0 + ext[i].1 / 2.0;
        let (step0, step1) = (center(1) - center(0), center(2) - center(1));
        assert!((step0 - step1).abs() < 0.011, "Jan to Feb and Feb to Mar are one step apart: {step0} {step1}");
        assert!(center(0).abs() < 0.011, "the first date sits on the plot's left edge: {}", center(0));
    }

    /// A point scale over the dates leaves a bar of an authored size at that size.
    #[test]
    fn value_labels_keep_an_authored_bar_size_on_the_point_scale() {
        let mut p = with_value_labels(make_time_payload(&months_from_2000(72)));
        p["resolved"]["style"]["mark"]["size"] = serde_json::json!(3.0);
        let ext = bar_extents(&render(&make_payload(p), &test_fonts()).expect("renders"));
        assert!(ext.iter().all(|&(_, w)| (w - 3.0).abs() < 0.011), "{ext:?}");
    }

    /// An authored x domain is the point scale's whole domain: a date it does not name has no position, and Vega
    /// draws its bar at the plot's left edge.
    #[test]
    fn value_labels_with_an_authored_domain_draw_unnamed_dates_at_the_left_edge() {
        let mut p = with_value_labels(make_time_payload(&months_from_2000(72)));
        p["resolved"]["style"]["axis_x"]["scale"] =
            serde_json::json!({"padding": 0.0, "continuous": {"domain": ["1999-01-01", "2007-01-01"]}});
        let ext = bar_extents(&render(&make_payload(p), &test_fonts()).expect("renders"));
        assert_eq!(ext.len(), 72);
        assert!(ext.iter().all(|&(x, w)| x.abs() < 0.011 && (w - 18.0).abs() < 0.011), "{ext:?}");
    }

    /// A column past the ordinal budget whose labels Vega would tick by interval: the point scale throws, so the
    /// chart paints no marks.
    #[test]
    fn value_labels_over_unthinned_ticks_raise_painted_no_marks() {
        let dates: Vec<String> = (0..130).map(|i| format!("{}-01-01", 1900 + i)).collect();
        let mut p = with_value_labels(make_time_payload(&dates));
        p["resolved"]["style"]["axis_x"]["labels"]["tilt_increments"] = serde_json::json!([0.0, -30.0, -45.0, -60.0, -90.0]);
        // A dense year axis steps to every 5th/10th year (`overlap.skip`); only an unthinned one ticks by interval.
        p["resolved"]["style"]["axis_x"]["labels"]["overlap"] = serde_json::json!({"skip": false, "tilt": true});
        let err = render(&make_payload(p), &test_fonts()).expect_err("raises");
        assert_eq!(err.code, "ERR-CHART-PAINTED-NO-MARKS", "{err:?}");
    }

    /// The four-digit text nodes of a render: the year labels of a year axis.
    fn svg_year_labels(svg: &str) -> Vec<i32> {
        svg.split('>')
            .filter_map(|chunk| chunk.split('<').next())
            .filter(|t| t.len() == 4 && t.bytes().all(|b| b.is_ascii_digit()))
            .map(|t| t.parse().unwrap())
            .collect()
    }

    /// The same column, thinned by the ladder to every 10th year, ticks nothing by interval: it renders, and
    /// only the stepped years are labeled.
    #[test]
    fn value_labels_over_a_year_stepped_axis_render_only_the_stepped_years() {
        let dates: Vec<String> = (0..130).map(|i| format!("{}-01-01", 1900 + i)).collect();
        let mut p = with_value_labels(make_time_payload(&dates));
        p["resolved"]["style"]["axis_x"]["labels"]["tilt_increments"] = serde_json::json!([0.0, -30.0, -45.0, -60.0, -90.0]);
        let svg = render(&make_payload(p), &test_fonts()).expect("a stepped year axis renders");
        let years = svg_year_labels(&svg);
        assert!(!years.is_empty() && years.iter().all(|y| y % 10 == 0 || y % 5 == 0), "{years:?}");
        assert!(years.len() < 130 / 4, "{years:?}");
    }

    /// A bar on a continuous (utc) time scale: the native interval ticks yield to the stepped labels.
    #[test]
    fn dense_year_axis_is_stepped_on_a_continuous_time_scale() {
        let dates: Vec<String> = (0..130).map(|i| format!("{}-01-01", 1900 + i)).collect();
        let mut p = make_time_payload(&dates);
        p["resolved"]["style"]["axis_x"]["labels"]["tilt_increments"] = serde_json::json!([0.0, -30.0, -45.0, -60.0, -90.0]);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let years = svg_year_labels(&svg);
        assert!(!years.is_empty() && years.len() < 40 && years.iter().all(|y| y % 5 == 0), "{years:?}");
    }

    /// The same column typed ordinal: stepped too.
    #[test]
    fn dense_year_axis_is_stepped_on_an_ordinal_time_scale() {
        let dates: Vec<String> = (0..130).map(|i| format!("{}-01-01", 1900 + i)).collect();
        let mut p = make_time_payload(&dates);
        p["resolved"]["style"]["axis_x"]["type"] = serde_json::json!("ordinal");
        p["resolved"]["style"]["axis_x"]["labels"]["tilt_increments"] = serde_json::json!([0.0, -30.0, -45.0, -60.0, -90.0]);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let years = svg_year_labels(&svg);
        assert!(!years.is_empty() && years.len() < 40 && years.iter().all(|y| y % 5 == 0), "{years:?}");
    }

    /// A point scale measures a bar's width against the date after the first: when that date is not the closest
    /// to it, the width is NaN and nothing paints.
    #[test]
    fn value_labels_on_dates_whose_first_gap_is_not_the_closest_raise_painted_no_marks() {
        let dates: Vec<String> =
            ["2024-01-03", "2024-01-20", "2024-02-14", "2024-02-21", "2024-05-21", "2024-06-09"].map(String::from).to_vec();
        let err = render(&make_payload(with_value_labels(make_time_payload(&dates))), &test_fonts()).expect_err("raises");
        assert_eq!(err.code, "ERR-CHART-PAINTED-NO-MARKS", "{err:?}");
    }

    /// An authored size centers a bucketed bar on its bucket at the literal width.
    #[test]
    fn an_authored_size_centers_a_bucketed_bar_on_its_bucket() {
        let _locale = crate::time_locale::en_us_scope();
        let mut p = make_time_payload(&months_from_2000(72));
        p["resolved"]["style"]["mark"]["size"] = serde_json::json!(3.0);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let ext = bar_extents(&svg);
        assert_eq!(ext.len(), 72);
        assert!(ext.iter().all(|&(_, w)| (w - 3.0).abs() < 0.011), "{ext:?}");
        let center = |i: usize| ext[i].0 + ext[i].1 / 2.0;
        let (d0, d1) = (center(0), center(1));
        // Jan 2000 and Feb 2000 center on their buckets' midpoints: 15.5 and 14.5 days
        // past their starts, the two starts 31 days apart.
        let px_per_day = (d1 - d0) / ((31.0 + 29.0) / 2.0);
        assert!(px_per_day > 0.0 && (d0 - 15.5 * px_per_day).abs() < 0.6 + 0.1, "first center {d0}, {px_per_day} px a day");
    }

    /// A grain dropped for the scaffold budget (a billing start beside month ends) leaves a continuous axis
    /// that still labels by the curated ladder over the span, not d3's full month names.
    #[test]
    fn a_dropped_grain_axis_labels_by_the_ladder_not_full_month_names() {
        let _locale = crate::time_locale::en_us_scope();
        let dates: Vec<String> = ["2025-08-03","2025-08-31","2025-09-30","2025-10-31","2025-11-30","2025-12-31","2026-01-31","2026-02-28","2026-03-31","2026-04-30","2026-05-31","2026-06-30","2026-07-31","2026-08-31"].map(String::from).to_vec();
        let svg = render(&make_payload(make_time_payload(&dates)), &test_fonts()).expect("renders");
        for name in ["August", "September", "October", "December"] {
            assert!(!svg.contains(&format!(">{name}<")), "{name}: {svg}");
        }
        for label in ["Sep", "Jan", "2025", "2026"] {
            assert!(svg.contains(&format!(">{label}<")), "{label}: {svg}");
        }
    }

    /// Five dates a century apart drop their yearly grain; the ladder labels the span by horizontal round
    /// steps, one label per tick, where d3 would have ticked every twenty.
    #[test]
    fn a_dropped_grain_year_axis_steps_the_ladder_horizontally() {
        let _locale = crate::time_locale::en_us_scope();
        let dates: Vec<String> = ["1800", "1900", "1950", "2000", "2025"].map(|y| format!("{y}-01-01")).to_vec();
        let mut p = make_time_payload(&dates);
        p["width"] = serde_json::json!(360.0);
        p["resolved"]["style"]["axis_x"]["labels"]["tilt_increments"] = serde_json::json!([0.0, -30.0, -45.0, -60.0, -90.0]);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(!svg.contains("rotate("), "{svg}");
        for year in ["1800", "1900", "2000"] {
            assert!(svg.contains(&format!(">{year}<")), "{year}: {svg}");
        }
        assert!(svg.contains(">1850<") && !svg.contains(">1820<"), "fifty-year steps at this width: {svg}");
    }

    /// Dates no calendar grain explains sit on their instants, every bar the
    /// ladder's one width: the clamp over the closest pair of distinct dates.
    #[test]
    fn irregular_dates_sit_on_their_instants_at_one_width() {
        let _locale = crate::time_locale::en_us_scope();
        let dates: Vec<String> =
            ["2024-01-03", "2024-01-10", "2024-02-14", "2024-03-02", "2024-05-21", "2024-06-09"].map(String::from).to_vec();
        let svg = render(&make_payload(make_time_payload(&dates)), &test_fonts()).expect("renders");
        let ext = bar_extents(&svg);
        assert_eq!(ext.len(), 6);
        let w = ext[0].1;
        assert!(ext.iter().all(|&(_, ew)| (ew - w).abs() < 0.011), "{ext:?}");
        let center = |i: usize| ext[i].0 + ext[i].1 / 2.0;
        // 2024-01-03 -> 2024-01-10 is 7 days, 2024-01-10 -> 2024-02-14 is 35.
        let ratio = (center(2) - center(1)) / (center(1) - center(0));
        assert!((ratio - 5.0).abs() < 0.02, "centers proportional to dates: {ratio}");
        assert!(w > 15.0, "the clamp ladder's width, not a day bucket's: {w}");
        assert!(!svg.contains("stroke=\"#DFE1E5\""), "no opener ticks without a thinned grain");
    }

    fn refusal(p: serde_json::Value) -> String {
        render(&make_payload(p), &test_fonts()).expect_err("refuses").to_string()
    }

    /// A negative bucket hangs below the zero baseline on the signed value ladder.
    #[test]
    fn a_negative_value_on_a_time_axis_draws_below_the_baseline() {
        let _locale = crate::time_locale::en_us_scope();
        let mut p = make_time_payload(&months_from_2000(72));
        p["rows"][3]["revenue"] = serde_json::json!(-5.0);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert_eq!(bar_fills(&svg).len(), 72);
        let positive = render(&make_payload(make_time_payload(&months_from_2000(72))), &test_fonts()).expect("renders");
        assert_ne!(svg, positive, "the negative bucket draws a different bar");
    }

    #[test]
    fn a_ranged_bar_on_a_time_axis_draws_one_bar_per_bucket() {
        let _locale = crate::time_locale::en_us_scope();
        let mut p = make_time_payload(&months_from_2000(72));
        p["resolved"]["y_start"] = serde_json::json!("start");
        for row in p["rows"].as_array_mut().unwrap() {
            row["start"] = serde_json::json!(50.0);
        }
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert_eq!(bar_fills(&svg).len(), 72);
    }

    /// A second row of one bucket stacks over the first, where the bar chart's stack groups by the bucket.
    #[test]
    fn two_rows_in_one_bucket_stack() {
        let _locale = crate::time_locale::en_us_scope();
        let mut p = make_time_payload(&months_from_2000(72));
        p["rows"].as_array_mut().unwrap().push(serde_json::json!({"product": "2000-02-01", "revenue": 5.0}));
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert_eq!(bar_fills(&svg).len(), 73);
    }

    #[test]
    fn two_rows_at_one_instant_stack() {
        let _locale = crate::time_locale::en_us_scope();
        let dates: Vec<String> =
            ["2024-01-03", "2024-01-10", "2024-02-14", "2024-03-02", "2024-05-21", "2024-05-21"].map(String::from).to_vec();
        let svg = render(&make_payload(make_time_payload(&dates)), &test_fonts()).expect("renders");
        assert_eq!(bar_fills(&svg).len(), 6);
    }

    /// A date range a query filter proves pins the time axis to the range, so the first bar sits where its
    /// month does inside it rather than at the plot's left edge.
    #[test]
    fn a_date_filtered_bar_pins_its_domain_to_the_range() {
        let _locale = crate::time_locale::en_us_scope();
        let plain = bar_extents(&render(&make_payload(make_time_payload(&months_from_2000(72))), &test_fonts()).expect("renders"));
        let mut p = make_time_payload(&months_from_2000(72));
        p["resolved"]["x_date_range"] = serde_json::json!({"start": "1999-01-01", "end": "2007-01-01"});
        let pinned = bar_extents(&render(&make_payload(p), &test_fonts()).expect("renders"));
        assert_eq!(pinned.len(), 72);
        assert!(pinned[0].0 > plain[0].0 + 30.0, "a year of slack before Jan 2000: {} against {}", pinned[0].0, plain[0].0);
    }

    /// Production's tooltip header formats the date the row carried, not the bucket start the bar sits at, and
    /// its stack total sums the rows of that date.
    #[test]
    fn a_time_axis_tooltip_names_the_rows_own_date() {
        let _locale = crate::time_locale::en_us_scope();
        let mut p = make_time_payload(&["2024-01-03".to_string(), "2024-01-10".to_string(), "2024-02-14".to_string(), "2024-03-02".to_string()]);
        p["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.0f");
        p["resolved"]["style"]["axis_x"]["time_unit"] = serde_json::json!("yearmonth");
        p["resolved"]["style"]["axis_x"]["type"] = serde_json::json!("temporal");
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        // The rows hold no calendar grain of their own, so the header is the day-level date, not "Jan 2024".
        for header in ["3 Jan 2024", "10 Jan 2024", "14 Feb 2024", "2 Mar 2024"] {
            assert!(svg.contains(&format!("{header}; ")), "{header}: {svg}");
        }
        assert!(!svg.contains("\u{2061}Jan 2024; "), "{svg}");
    }

    /// Timestamps within a day tick the clock every six hours and read its words, where the same instants a
    /// day apart would tick the calendar.
    #[test]
    fn timestamps_within_a_day_label_the_clock() {
        let _locale = crate::time_locale::en_us_scope();
        let dates: Vec<String> = (0..24).map(|h| format!("2024-03-05T{h:02}:00:00")).collect();
        let mut p = make_time_payload(&dates);
        p["resolved"]["style"]["axis_x"]["type"] = serde_json::json!("temporal");
        p["resolved"]["style"]["axis_x"]["labels"]["clock"] = serde_json::json!(12);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        for word in [">Midnight<", ">6am<", ">Noon<", ">6pm<"] {
            assert!(svg.contains(word), "{word}: {svg}");
        }
    }

    /// A genuine zero adds the zero-label text layer, which makes the spec layered: the hover band's invisible
    /// bars, which only an unlayered bar declares, are absent.
    #[test]
    fn a_genuine_zero_leaves_the_bar_no_hover_band() {
        let _locale = crate::time_locale::en_us_scope();
        let hover = |zero: bool| {
            let mut p = make_time_payload(&months_from_2000(72));
            p["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.0f");
            p["resolved"]["style"]["mark"]["labels"] = serde_json::json!({
                "visible": false, "position": "above", "field": null, "format": null, "dx": null, "dy": null, "font": null
            });
            p["resolved"]["style"]["axis_y"]["domain_max"] = serde_json::json!(180.0);
            p["resolved"]["style"]["axis_y"]["tick_values"] = serde_json::json!([0.0, 100.0]);
            // A bar short of the domain's top needs the hover band's room; 3 is under
            // its trigger share of the 171 max, yet long enough to draw.
            p["rows"][3]["revenue"] = serde_json::json!(if zero { 0.0 } else { 3.0 });
            render(&make_payload(p), &test_fonts()).expect("renders").contains("opacity=\"0\" data-dct-role=\"bar\"")
        };
        assert!(hover(false), "an unlayered bar keeps its hover band");
        assert!(!hover(true), "a zero's label layer takes it away");
    }

    /// With one date there is no pair to size a bar from: it takes the width ceiling, centered.
    #[test]
    fn a_single_instant_draws_one_bar_at_the_width_ceiling() {
        let _locale = crate::time_locale::en_us_scope();
        let svg = render(&make_payload(make_time_payload(&["2000-01-03".to_string()])), &test_fonts()).expect("renders");
        let bars = bar_extents(&svg);
        assert_eq!(bars.len(), 1);
        assert!((bars[0].1 - CONTINUOUS_BAR_MAX_SIZE).abs() < 0.011, "{bars:?}");
    }

    /// A time of day is a fraction of its day: two instants a half day apart sit a half day's pixels apart.
    #[test]
    fn timestamps_sit_at_their_instant_within_a_day() {
        let _locale = crate::time_locale::en_us_scope();
        let dates: Vec<String> =
            ["2024-01-03T06:00:00", "2024-01-03T18:00:00", "2024-02-14T00:00:00"].map(String::from).to_vec();
        let bars = bar_extents(&render(&make_payload(make_time_payload(&dates)), &test_fonts()).expect("renders"));
        assert_eq!(bars.len(), 3);
        let center = |i: usize| bars[i].0 + bars[i].1 / 2.0;
        let ratio = (center(1) - center(0)) / (center(2) - center(1));
        assert!((ratio - 0.5 / 41.25).abs() < 0.0005, "half a day over 41.25 days: {ratio}");
    }

    /// A measure with no number leaves the time axis and no bars.
    #[test]
    fn a_time_axis_with_no_numeric_measure_draws_no_bars() {
        let _locale = crate::time_locale::en_us_scope();
        let mut p = make_time_payload(&months_from_2000(72));
        for row in p["rows"].as_array_mut().unwrap() {
            row["revenue"] = serde_json::Value::Null;
        }
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(bar_fills(&svg).is_empty());
        assert!(svg.contains(">2005</text>"), "the axis still labels its years");
    }

    /// Vega's `timeunit` yields NaN for a year below 100, so no bar has a position.
    #[test]
    fn a_year_below_one_hundred_raises_painted_no_marks() {
        let dates: Vec<String> = (0..72).map(|i| format!("{:04}-{:02}-01", 99 + i / 12, i % 12 + 1)).collect();
        let err = render(&make_payload(make_time_payload(&dates)), &test_fonts()).expect_err("raises");
        assert_eq!(err.code, "ERR-CHART-PAINTED-NO-MARKS", "{err:?}");
    }

    /// An authored time format on a continuous temporal x draws on the time axis.
    #[test]
    fn an_authored_time_format_labels_the_time_axis() {
        let mut p = make_time_payload(&months_from_2000(72));
        p["resolved"]["style"]["axis_x"]["labels"]["format"] = serde_json::json!("%Y-%m");
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(svg.contains(">2005-01</text>"), "{svg}");
    }

    /// An authored `min_size` above the ceiling is the width, as Vega's `max(min, min(max, x))`.
    #[test]
    fn a_min_size_above_the_ceiling_is_the_continuous_bar_width() {
        let mut p = make_continuous_payload(&[(0.0, 100.0), (8.0, 200.0), (16.0, 300.0)]);
        p["resolved"]["style"]["mark"]["min_size"] = serde_json::json!(30.0);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let ws = bar_widths(&svg);
        assert!(!ws.is_empty() && ws.iter().all(|w| (w - 30.0).abs() < 1e-6), "{ws:?}");
    }

    /// Sparse points: the ceiling binds — width caps at max_size.
    #[test]
    fn continuous_x_sparse_points_cap_at_the_ceiling() {
        let payload = make_payload(make_continuous_payload(&[
            (0.0, 100.0),
            (8.0, 200.0),
            (16.0, 300.0),
        ]));
        let svg = render(&payload, &test_fonts()).expect("sparse renders");
        let ws = bar_widths(&svg);
        assert!(!ws.is_empty() && ws.iter().all(|w| (w - 20.0).abs() < 1e-6), "{ws:?}");
    }

    /// Dense points: the floor binds — bars overlap at min_size, never
    /// squeezed thinner.
    #[test]
    fn continuous_x_dense_points_hold_the_floor() {
        let xs: Vec<(f64, f64)> = (0..150).map(|m| (m as f64, 100.0)).collect();
        let payload = make_payload(make_continuous_payload(&xs));
        let svg = render(&payload, &test_fonts()).expect("dense renders");
        let ws = bar_widths(&svg);
        assert_eq!(ws.len(), 150);
        assert!(ws.iter().all(|w| (w - 4.0).abs() < 1e-6), "floor 4: {:?}", &ws[..3]);
    }

    /// The vertices of the first path stroked `color`, as `(x, y)` pairs.
    fn stroked_path_points(svg: &str, color: &str) -> Vec<(f64, f64)> {
        let tag = svg
            .split("<path ")
            .find(|tag| tag.split("/>").next().is_some_and(|t| t.contains(&format!("stroke=\"{color}\"")) && t.contains("fill=\"none\"")))
            .expect("a stroked layer path");
        let d = tag.split("d=\"").nth(1).unwrap().split('"').next().unwrap();
        d.split(['M', 'L'])
            .filter(|pair| !pair.is_empty())
            .map(|pair| {
                let (x, y) = pair.split_once(',').unwrap();
                (x.parse().unwrap(), y.parse().unwrap())
            })
            .collect()
    }

    /// A layer shares the bars' linear x scale and the base's width ladder: its
    /// vertices land on the bar centers, at whatever pixel spacing the plot gives
    /// the data, never on band positions.
    #[test]
    fn a_line_layer_on_a_numeric_x_draws_on_the_linear_scale() {
        let xs: Vec<(f64, f64)> = (0..4).map(|h| (h as f64 * 6.0, 100.0 + h as f64)).collect();
        let mut json = make_continuous_payload(&xs);
        for row in json["rows"].as_array_mut().unwrap() {
            row["target"] = serde_json::json!(50.0);
        }
        let bare = render(&make_payload(json.clone()), &test_fonts()).expect("renders without the layer");
        json["resolved"]["layers"] = serde_json::json!([line_layer_json("#e45756")]);
        let svg = render(&make_payload(json), &test_fonts()).expect("renders with the layer");
        assert_eq!(bar_widths(&svg), bar_widths(&bare), "the layer leaves the bars' width ladder alone");
        let points = stroked_path_points(&svg, "#e45756");
        assert_eq!(points.len(), 4, "{points:?}");
        let step = points[1].0 - points[0].0;
        assert!(points.windows(2).all(|w| (w[1].0 - w[0].0 - step).abs() < 2e-3), "evenly spaced like the x values: {points:?}");
    }

    /// A bar layer on a scale pinned away from the 0 baseline clips to the plot
    /// (`_baseline_outside_shared_domain`); one on a scale that holds 0 does not.
    #[test]
    fn a_bar_layer_on_a_numeric_x_clips_only_when_the_scale_excludes_zero() {
        let xs: Vec<(f64, f64)> = (0..4).map(|h| (h as f64 * 6.0, 100.0 + h as f64)).collect();
        let mut json = make_continuous_payload(&xs);
        for row in json["rows"].as_array_mut().unwrap() {
            row["target"] = serde_json::json!(110.0);
        }
        json["resolved"]["layers"] = serde_json::json!([{
            "type": "bar",
            "bar_mark": {
                "border": {"width": 1.0, "color": "#FFFFFF", "radius": 2.0},
                "band_width": 0.8, "size": 8.0,
                "labels": {"visible": false}
            },
            "axis_y": {"position": null},
            "x": null, "y": "target", "label": "Target", "color": null, "ink": "#779bc9", "query_name": null
        }]);
        let open = render(&make_payload(json.clone()), &test_fonts()).expect("renders");
        assert!(!open.contains("dbt-layer-clip"), "{open}");
        json["resolved"]["style"]["axis_y"]["domain_min"] = serde_json::json!(90.0);
        let zoomed = render(&make_payload(json), &test_fonts()).expect("renders");
        assert!(zoomed.contains("dbt-layer-clip"), "{zoomed}");
    }

    /// The min ADJACENT step drives the width — an uneven cluster's tight
    /// pair sets it for every bar (never the mean spacing).
    #[test]
    fn continuous_x_min_step_not_mean_drives_the_width() {
        // steps: 1,1,1,12,12 — min 1, mean 5.4. At ~18px/unit the min step
        // lands mid-ladder (~15px) where the mean would cap at 20.
        let payload = make_payload(make_continuous_payload(&[
            (0.0, 100.0),
            (1.0, 110.0),
            (2.0, 120.0),
            (3.0, 130.0),
            (15.0, 140.0),
            (27.0, 150.0),
        ]));
        let svg = render(&payload, &test_fonts()).expect("clustered renders");
        let ws = bar_widths(&svg);
        assert!(
            !ws.is_empty() && ws.iter().all(|w| (4.0 + 1.0..20.0 - 1.0).contains(w)),
            "min step, not mean, drives the width: {ws:?}"
        );
    }

    /// A theme payload missing a ladder field errors with the field name
    /// (production asserts each individually when `mark.size` is unset) —
    /// never a silent default gutter.
    #[test]
    fn continuous_x_missing_gap_errors_with_the_field_name() {
        let xs: Vec<(f64, f64)> = (0..24).map(|h| (h as f64, 100.0)).collect();
        let mut payload_json = make_continuous_payload(&xs);
        payload_json["resolved"]["style"]["mark"]["gap"] = serde_json::Value::Null;
        let payload = make_payload(payload_json);
        let err = render(&payload, &test_fonts()).expect_err("missing gap is a contract violation");
        assert!(err.contains("mark.gap"), "{err}");
    }

    fn mirrored_zero_labels(mirror: serde_json::Value) -> usize {
        let mut payload_json = make_vertical_payload(&[("A", 10.0), ("B", 20.0)], false);
        payload_json["resolved"]["style"]["axis_y"]["mirror"] = mirror;
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("a mirrored bar renders");
        svg.matches(">0<").count()
    }

    /// `axis_y.mirror` repeats the value labels on the opposite edge, and the
    /// production `ResolvedMirrorAxis` object is the same request as `true`.
    #[test]
    fn mirror_repeats_the_value_labels_on_the_opposite_edge() {
        let plain = mirrored_zero_labels(serde_json::json!(false));
        assert_eq!(mirrored_zero_labels(serde_json::json!(true)), plain + 1);
        assert_eq!(
            mirrored_zero_labels(serde_json::json!({"format": null, "expr": null, "ruler": null, "tick_label": null})),
            plain + 1
        );
    }

    /// A descending authored pair inverts Vega's axis: 0 sits at the plot top
    /// and the bars hang from it.
    #[test]
    fn descending_authored_y_domain_hangs_the_bars_from_the_plot_top() {
        let mut payload_json = make_authored_domain_payload(false);
        payload_json["resolved"]["style"]["axis_y"]["scale"] =
            serde_json::json!({"continuous": {"domain": [4000.0, 0.0]}});
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("a descending domain renders");
        let grid = gridline_positions(&svg, "y1");
        let (top, bottom) = (grid.iter().copied().fold(f64::INFINITY, f64::min), grid.iter().copied().fold(0.0, f64::max));
        let rects = parse_rects(&svg);
        assert_eq!(rects.len(), 2, "{rects:?}");
        for (rect, value) in rects.iter().zip([320.0, 540.0]) {
            assert!((rect.1 - top).abs() < 1.0, "bar {value} hangs from the top rung {top}: {rect:?}");
            assert!((rect.3 - value / 4000.0 * (bottom - top)).abs() < 1.5, "bar {value}: {rect:?}");
        }
    }

    // ── Authored measure domain (both orientations) ──────────────────────

    /// A two-bar chart against the authored domain `[0, 4000]` with the
    /// resolve-baked ladder `nice_tick_values(0, 4000, 5)` — an authored
    /// domain leaves `domain_max`/`domain_min` unbaked, so the ladder is the
    /// only thing that reaches the payload from `ticks.count: 5`.
    fn make_authored_domain_payload(horizontal: bool) -> serde_json::Value {
        use serde_json::json;
        let mut payload_json = if horizontal {
            make_horizontal_payload()
        } else {
            make_vertical_payload(&[("A", 320.0), ("B", 540.0)], false)
        };
        payload_json["rows"] = json!([
            {"product": "A", "revenue": 320.0},
            {"product": "B", "revenue": 540.0},
        ]);
        payload_json["resolved"]["style"]["mark"]["border"]["radius"] = json!(0.0);
        let axis_y = &mut payload_json["resolved"]["style"]["axis_y"];
        axis_y["scale"] = json!({"continuous": {"domain": [0.0, 4000.0]}});
        axis_y["tick_values"] = json!([0.0, 1000.0, 2000.0, 3000.0, 4000.0]);
        axis_y["domain_max"] = json!(null);
        payload_json
    }

    /// The numeric tick labels the value axis paints, in paint order.
    fn value_axis_labels(svg: &str) -> Vec<String> {
        svg.match_indices("<text ")
            .filter_map(|(start, _)| {
                let open_end = svg[start..].find('>')? + start + 1;
                let text = &svg[open_end..open_end + svg[open_end..].find('<')?];
                let numeric = !text.is_empty()
                    && text.chars().all(|c| c.is_ascii_digit() || matches!(c, ',' | '.' | '\u{2212}'));
                numeric.then(|| text.to_string())
            })
            .collect()
    }

    /// The `attr` (`y1` for a vertical bar's gridlines, `x1` for a
    /// horizontal one's) of every gridline, in paint order.
    fn gridline_positions(svg: &str, attr: &str) -> Vec<f64> {
        svg.match_indices("<line ")
            .filter_map(|(start, _)| {
                let tag = &svg[start..start + svg[start..].find('>')?];
                tag.contains("stroke=\"#D0D3D9\"").then(|| {
                    tag.split(&format!("{attr}=\"")).nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
                })
            })
            .collect()
    }

    /// `ticks.count: 5` over the authored domain `[0, 4000]`: the drawn
    /// domain is the authored pair verbatim and the axis draws the baked
    /// ladder inside it (`_emit_vertical`'s `authored_domain` branch emits
    /// `scale.domain`; `bake_tick_ladder` writes `axis.values`). A domain
    /// read from the data extent instead filters away every rung above the
    /// tallest bar, leaving only the `0` label.
    #[test]
    fn vertical_bar_authored_domain_draws_the_baked_ladder_over_the_authored_extent() {
        let svg = render(&make_payload(make_authored_domain_payload(false)), &test_fonts())
            .expect("renders");
        assert_eq!(value_axis_labels(&svg), ["0", "1,000", "2,000", "3,000", "4,000"], "{svg}");

        let grid = gridline_positions(&svg, "y1");
        assert_eq!(grid.len(), 5, "one gridline per rung: {grid:?}");
        // Gridlines snap to whole pixels (Vega's tickRound), so the plot
        // height read off them (rung 0 at the bottom) is good to a pixel.
        let plot_h = grid[0] - grid[4];
        let rects = parse_rects(&svg);
        assert_eq!(rects.len(), 2, "{rects:?}");
        assert!(
            (rects[1].3 - 540.0 / 4000.0 * plot_h).abs() < 1.0,
            "the 540 bar spans 540/4000 of the plot, not the data extent: {:?} in plot_h {plot_h}",
            rects[1]
        );
    }

    /// The same law on the horizontal arm, whose measure axis is x.
    #[test]
    fn horizontal_bar_authored_domain_draws_the_baked_ladder_over_the_authored_extent() {
        let svg = render(&make_payload(make_authored_domain_payload(true)), &test_fonts())
            .expect("renders");
        assert_eq!(value_axis_labels(&svg), ["0", "1,000", "2,000", "3,000", "4,000"], "{svg}");

        let grid = gridline_positions(&svg, "x1");
        assert_eq!(grid.len(), 5, "one gridline per rung: {grid:?}");
        let plot_w = grid[4] - grid[0];
        // Horizontal single-series bars draw value-descending: 540 first.
        let widest = parse_rects(&svg)[0].2;
        assert!(
            (widest - 540.0 / 4000.0 * plot_w).abs() < 1.0,
            "the 540 bar spans 540/4000 of the plot: {widest} in plot_w {plot_w}"
        );
    }

    /// A domain that excludes 0 compiles to different scales per orientation
    /// (probed: vertical `zero: false`, whose zero-based bars hang past the
    /// plot, horizontal `zero: true`, which widens it to 0).
    #[test]
    fn authored_domain_excluding_zero_widens_a_horizontal_bar_and_hangs_a_vertical_one_past_the_plot() {
        for horizontal in [false, true] {
            let mut payload_json = make_authored_domain_payload(horizontal);
            payload_json["resolved"]["style"]["axis_y"]["scale"] =
                serde_json::json!({"continuous": {"domain": [100.0, 4000.0]}});
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            let grid = gridline_positions(&svg, if horizontal { "x1" } else { "y1" });
            let rects = parse_rects(&svg);
            if horizontal {
                let left = grid.iter().copied().fold(f64::INFINITY, f64::min);
                assert!(rects.iter().all(|r| (r.0 - left).abs() < 1.0 || r.0 < left), "{rects:?} vs {left}");
            } else {
                let bottom = grid.iter().copied().fold(0.0, f64::max);
                assert!(rects.iter().all(|r| r.1 + r.3 > bottom + 1.0), "{rects:?} vs {bottom}");
            }
        }
    }

    /// Vega draws a bar outside an authored pair unclipped: 5000 over
    /// `[0, 4000]` overflows the plot top, -300 hangs below the bottom edge.
    #[test]
    fn authored_domain_with_a_value_outside_it_draws_the_bar_past_the_plot() {
        for (horizontal, value) in [(false, 5000.0), (true, 5000.0), (false, -300.0), (true, -300.0)] {
            let mut payload_json = make_authored_domain_payload(horizontal);
            payload_json["rows"][0]["revenue"] = serde_json::json!(value);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            let grid = gridline_positions(&svg, if horizontal { "x1" } else { "y1" });
            let (lo, hi) = (grid.iter().copied().fold(f64::INFINITY, f64::min), grid.iter().copied().fold(0.0, f64::max));
            let rects = parse_rects(&svg);
            let past = rects.iter().any(|r| match (horizontal, value > 0.0) {
                (false, true) => r.1 < lo - 1.0,
                (false, false) => r.1 + r.3 > hi + 1.0,
                (true, true) => r.0 + r.2 > hi + 1.0,
                (true, false) => r.0 < lo - 1.0,
            });
            assert!(past, "horizontal={horizontal} value={value}: {rects:?} in [{lo}, {hi}]");
        }
    }

    /// A value far past the authored pair grows the fit's ink until the plot
    /// collapses: every mark is zero-sized, which production reports as
    /// ERR-CHART-PAINTED-NO-MARKS rather than a too-small card.
    #[test]
    fn authored_domain_far_below_the_data_collapses_the_plot_to_painted_no_marks() {
        for horizontal in [false, true] {
            let mut payload_json = make_authored_domain_payload(horizontal);
            payload_json["resolved"]["style"]["axis_y"]["scale"] =
                serde_json::json!({"continuous": {"domain": [0.0, 1.0]}});
            let err = render(&make_payload(payload_json), &test_fonts()).expect_err("collapses");
            assert!(err.contains("ERR-CHART-PAINTED-NO-MARKS"), "horizontal={horizontal}: {err}");
        }
    }

    /// An authored pair pinning one value gives the scale no extent: every bar is
    /// zero-sized, in either orientation, and `nice` rounds the pair first.
    #[test]
    fn an_authored_domain_pinning_one_value_raises_painted_no_marks() {
        for horizontal in [false, true] {
            let mut payload_json = make_authored_domain_payload(horizontal);
            payload_json["resolved"]["style"]["axis_y"]["scale"] =
                serde_json::json!({"continuous": {"domain": [5.0, 5.0]}});
            let err = render(&make_payload(payload_json), &test_fonts()).expect_err("collapses");
            assert!(err.contains("ERR-CHART-PAINTED-NO-MARKS"), "horizontal={horizontal}: {err}");
        }
    }

    /// A stacked bar scales to the authored pair; a category whose stack
    /// reaches past it paints past the plot top.
    #[test]
    fn authored_domain_on_a_stacked_bar_scales_to_the_pair() {
        let mut payload_json = make_stacked_payload("vertical", "zero");
        payload_json["resolved"]["style"]["axis_y"]["scale"] =
            serde_json::json!({"continuous": {"domain": [0.0, 1000.0]}});
        render(&make_payload(payload_json), &test_fonts()).expect("renders");
        let mut payload_json = make_stacked_payload("vertical", "zero");
        payload_json["resolved"]["style"]["axis_y"]["scale"] =
            serde_json::json!({"continuous": {"domain": [0.0, 30.0]}});
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        let top = gridline_positions(&svg, "y1").into_iter().fold(f64::INFINITY, f64::min);
        assert!(parse_rects(&svg).iter().any(|r| r.1 < top - 1.0), "a segment overshoots the top rung {top}: {svg}");
    }

    /// An authored `nice: true` rounds the authored pair after it is pinned
    /// (`configureDomain`'s last step): `[0, 3654]` draws as `[0, 4000]`.
    #[test]
    fn authored_domain_with_authored_nice_is_rounded_outward() {
        let mut payload_json = make_authored_domain_payload(false);
        payload_json["resolved"]["style"]["axis_y"]["scale"] =
            serde_json::json!({"nice": true, "continuous": {"domain": [0.0, 3654.0]}});
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        let grid = gridline_positions(&svg, "y1");
        let plot_h = grid[0] - grid[4];
        assert!(
            (parse_rects(&svg)[1].3 - 540.0 / 4000.0 * plot_h).abs() < 1.0,
            "the top rung (4000) is the niced domain's top, not 3654: {svg}"
        );
    }

    /// An authored domain with no baked ladder takes d3's ticks over the
    /// authored pair (Vega draws the scale's own ticks when `axis.values`
    /// is absent) — including the rungs below zero.
    #[test]
    fn horizontal_bar_authored_negative_domain_without_a_ladder_draws_d3_ticks_over_it() {
        let mut payload_json = make_authored_domain_payload(true);
        payload_json["resolved"]["style"]["axis_y"]["scale"] =
            serde_json::json!({"continuous": {"domain": [-100.0, 100.0]}});
        payload_json["rows"][0]["revenue"] = serde_json::json!(32.0);
        payload_json["rows"][1]["revenue"] = serde_json::json!(54.0);
        payload_json["resolved"]["style"]["axis_y"]["tick_values"] = serde_json::json!([]);
        payload_json["resolved"]["style"]["axis_y"]["ticks"]["count"] = serde_json::json!(5.0);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        assert_eq!(
            value_axis_labels(&svg),
            ["\u{2212}100", "\u{2212}50", "0", "50", "100"],
            "{svg}"
        );
        let grid = gridline_positions(&svg, "x1");
        assert!(
            (zero_rule_x(&svg) - grid[2]).abs() < 1.0,
            "the zero rule sits mid-plot at the 0 rung: {} vs {grid:?}",
            zero_rule_x(&svg)
        );
    }

    /// The quantitative-x arm draws an authored y pair: the 300 bar spans
    /// three quarters of `[0, 400]`.
    #[test]
    fn authored_domain_on_a_quantitative_x_bar_scales_to_the_pair() {
        let mut payload_json = make_continuous_payload(&[(0.0, 100.0), (1.0, 200.0), (2.0, 300.0)]);
        payload_json["resolved"]["style"]["axis_y"]["scale"] =
            serde_json::json!({"continuous": {"domain": [0.0, 400.0]}});
        payload_json["resolved"]["style"]["mark"]["border"]["radius"] = serde_json::json!(0.0);
        payload_json["resolved"]["style"]["axis_y"]["tick_values"] = serde_json::json!([0.0, 100.0, 200.0, 300.0, 400.0]);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        let grid = gridline_positions(&svg, "y1");
        let plot_h = grid.iter().copied().fold(0.0, f64::max) - grid.iter().copied().fold(f64::INFINITY, f64::min);
        let tallest = parse_rects(&svg).iter().map(|r| r.3).fold(0.0, f64::max);
        assert!((tallest - 0.75 * plot_h).abs() < 1.5, "{tallest} in plot_h {plot_h}: {svg}");
    }

    // ── Vertical zero-anchored negative floor ────────────────────────────

    /// Three single-series columns straddling zero on the ladder production
    /// bakes for them (`nice_tick_values(-30, 45·1.08, 6)`): the ladder's
    /// first rung, −40, is the drawn domain floor and `domain_max` carries
    /// the headroom (`_emit_vertical`'s `bar_zero` branch:
    /// `{"domainMin": -40, "zero": True}` + `apply_domain_headroom_bounds`).
    fn make_negative_floor_payload() -> serde_json::Value {
        use serde_json::json;
        let mut payload_json =
            make_vertical_payload(&[("A", -30.0), ("B", 45.0), ("C", 20.0)], false);
        payload_json["resolved"]["style"]["mark"]["border"]["radius"] = json!(0.0);
        let axis_y = &mut payload_json["resolved"]["style"]["axis_y"];
        axis_y["tick_values"] = json!([-40.0, -20.0, 0.0, 20.0, 40.0, 60.0]);
        axis_y["domain_max"] = json!(48.6);
        payload_json
    }

    /// The first zero-rule line's `y1` — the y of value 0 in plot-local
    /// coordinates.
    fn zero_rule_y(svg: &str) -> f64 {
        let start = svg.find("stroke=\"#8A8C8F\"").expect("zero rule drawn");
        let snippet = &svg[..start];
        let y1_start = snippet.rfind("y1=\"").expect("zero rule has y1");
        snippet[y1_start + 4..].split('"').next().unwrap().parse().unwrap()
    }

    /// The zero-anchored ladder's first rung is the domain floor, so a
    /// negative column hangs from the zero rule down to its own value and a
    /// positive one stands on the rule — not on the plot's bottom edge —
    /// and every rung down to the floor is labeled. A scale built from a
    /// 0 floor instead clamps the negative column to zero height and drops
    /// the sub-zero rungs off the plot.
    #[test]
    fn vertical_bar_negative_floor_draws_both_signs_from_the_zero_rule() {
        let svg = render(&make_payload(make_negative_floor_payload()), &test_fonts())
            .expect("renders");
        assert_eq!(
            value_axis_labels(&svg),
            ["\u{2212}40", "\u{2212}20", "0", "20", "40"],
            "the ladder's sub-zero rungs draw, and 60 sits past domain_max: {svg}"
        );

        let zero_y = zero_rule_y(&svg);
        let rects = parse_rects(&svg);
        assert_eq!(rects.len(), 3, "the negative column draws too: {rects:?}");
        let (neg, pos_b, pos_c) = (&rects[0], &rects[1], &rects[2]);
        assert!((neg.1 - zero_y).abs() < 0.1, "the negative column hangs from the rule: {neg:?} / {zero_y}");
        for pos in [pos_b, pos_c] {
            assert!(
                (pos.1 + pos.3 - zero_y).abs() < 0.1,
                "a positive column stands on the rule: {pos:?} / {zero_y}"
            );
        }
        // −30 and 45 sit on one linear scale: heights are value-proportional
        // (coordinates are emitted to two decimals).
        assert!((neg.3 / pos_b.3 - 30.0 / 45.0).abs() < 1e-3, "{neg:?} vs {pos_b:?}");
        // The −40 rung is the lowest gridline and the domain floor: it sits
        // 40/30 of the negative column's height below the rule (gridlines
        // snap to whole pixels).
        let grid = gridline_positions(&svg, "y1");
        assert!(
            (grid[0] - 0.5 - zero_y - neg.3 * 40.0 / 30.0).abs() < 1.0,
            "the domain floor is the first rung: {grid:?} / rule {zero_y} / column {neg:?}"
        );
    }

    /// A negative column rounds its far (bottom) end and stays square
    /// against the rule, as production's `cornerRadiusBottomLeft/Right` on
    /// the negative layer does (probed: the reference's negative bar path
    /// has square top corners and arcs at the bottom).
    #[test]
    fn vertical_bar_negative_column_rounds_its_bottom() {
        let mut payload_json = make_negative_floor_payload();
        payload_json["resolved"]["style"]["mark"]["border"]["radius"] = serde_json::json!(2.0);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        let bar_paths: Vec<&str> = svg
            .match_indices("<path d=\"M ")
            .map(|(i, _)| svg[i..].split('>').next().unwrap())
            .filter(|p| p.contains(" A "))
            .collect();
        assert_eq!(bar_paths.len(), 3, "{bar_paths:?}");
        assert!(bar_paths[0].contains(" 0 0,0 "), "negative rounds its bottom: {}", bar_paths[0]);
        assert!(bar_paths[1].contains(" 0 0,1 "), "positive rounds its top: {}", bar_paths[1]);
    }

    /// An authored domain with a negative floor reaches the vertical arm
    /// through the same floor: `[-100, 100]` over positive data draws the
    /// rule mid-plot and the columns standing on it.
    #[test]
    fn vertical_bar_authored_negative_domain_stands_its_columns_on_the_zero_rule() {
        let mut payload_json = make_authored_domain_payload(false);
        payload_json["resolved"]["style"]["axis_y"]["scale"] =
            serde_json::json!({"continuous": {"domain": [-100.0, 1000.0]}});
        payload_json["resolved"]["style"]["axis_y"]["tick_values"] =
            serde_json::json!([-100.0, 0.0, 500.0, 1000.0]);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        let zero_y = zero_rule_y(&svg);
        let grid = gridline_positions(&svg, "y1");
        let plot_h = grid[0] - grid[3];
        let rects = parse_rects(&svg);
        assert!((rects[1].1 + rects[1].3 - zero_y).abs() < 0.1, "{rects:?} / {zero_y}");
        assert!((rects[1].3 - 540.0 / 1100.0 * plot_h).abs() < 1.0, "{rects:?} / {plot_h}");
    }

    /// A zero row under a negative floor keeps its genuine-zero label.
    #[test]
    fn vertical_bar_negative_floor_with_a_genuine_zero_label_draws() {
        let mut zero_row = make_vertical_payload(&[("A", -30.0), ("B", 0.0), ("C", 20.0)], false);
        zero_row["resolved"]["style"]["axis_y"]["tick_values"] = serde_json::json!([-40.0, -20.0, 0.0, 20.0, 40.0]);
        zero_row["resolved"]["style"]["axis_y"]["domain_max"] = serde_json::json!(21.6);
        zero_row["resolved"]["style"]["mark"]["labels"] = serde_json::json!({
            "visible": false, "position": "above",
            "field": null, "format": null, "dx": null, "dy": null, "font": null
        });
        let svg = render(&make_payload(zero_row), &test_fonts()).expect("renders");
        assert!(svg.contains(">0</text>"), "{svg}");
    }

    /// No computed ladder means no rung to pin the floor from: Vega's `zero: true`
    /// extent is the data's own, so the negative column keeps its length.
    #[test]
    fn vertical_bar_negative_value_on_an_unbaked_axis_draws_to_the_data_floor() {
        let mut payload_json = make_negative_floor_payload();
        payload_json["resolved"]["style"]["axis_y"]["tick_values"] = serde_json::json!([]);
        payload_json["resolved"]["style"]["axis_y"]["domain_max"] = serde_json::json!(null);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("a negative value on an unbaked axis draws");
        assert!(svg.contains('\u{2212}'), "the data floor is on the axis: {svg}");
    }

    /// A zoomed (`zero: false`) floor above 0 leaves every column's zero
    /// baseline below the plot: production paints no marks for it (probed:
    /// `{"zero": false, "domainMin": 115.2, "domainMax": 184.8}` over
    /// 120/150/180 renders `ERR-CHART-PAINTED-NO-MARKS`).
    #[test]
    fn vertical_bar_zoomed_floor_above_zero_paints_no_marks() {
        use serde_json::json;
        let mut payload_json =
            make_vertical_payload(&[("A", 120.0), ("B", 150.0), ("C", 180.0)], false);
        let axis_y = &mut payload_json["resolved"]["style"]["axis_y"];
        axis_y["scale"] = json!({"continuous": {"zero": false}});
        axis_y["tick_values"] = json!([100.0, 150.0, 200.0]);
        axis_y["domain_min"] = json!(115.2);
        axis_y["domain_max"] = json!(184.8);
        let err = render(&make_payload(payload_json), &test_fonts())
            .expect_err("a zoomed floor above 0 paints no marks in production");
        assert_eq!(err.code, "ERR-CHART-PAINTED-NO-MARKS");
        assert_eq!(err.params["row_count"], 3);
    }

    /// A layer's marks (and an all-zero measure) change production's verdict,
    /// so only the plain refusal is honest there.
    #[test]
    fn a_zoomed_floor_beside_a_layer_or_over_zeros_is_not_the_painted_no_marks_code() {
        use serde_json::json;
        let zoomed = |payload_json: &mut serde_json::Value| {
            let axis_y = &mut payload_json["resolved"]["style"]["axis_y"];
            axis_y["scale"] = json!({"continuous": {"zero": false}});
            axis_y["tick_values"] = json!([100.0, 150.0, 200.0]);
            axis_y["domain_min"] = json!(115.2);
            axis_y["domain_max"] = json!(184.8);
        };
        let mut zeros = make_vertical_payload(&[("A", 0.0), ("B", 0.0)], false);
        zoomed(&mut zeros);
        let code = render(&make_payload(zeros), &test_fonts()).err().map(|err| err.code);
        assert_ne!(code.as_deref(), Some("ERR-CHART-PAINTED-NO-MARKS"));
    }

    /// The fit closure's tick-ink probe reads the single-series negative
    /// floor too: a chart whose ladder bottoms out at the floor negotiates
    /// the same plot rect as an all-positive chart with the same ladder
    /// shifted up. With a 0.0 probe the −10 rung's ink lands at 1.5·ph and
    /// forces a bottom margin the reference never reserves.
    #[test]
    fn fit_probe_reads_the_single_series_negative_floor() {
        use serde_json::json;
        let plot_rect = |rows: serde_json::Value, ticks: serde_json::Value, dmax: f64| {
            let mut payload_json = make_vertical_payload(&[("A", 0.0)], false);
            payload_json["rows"] = rows;
            payload_json["resolved"]["style"]["mark"]["border"]["radius"] = json!(0.0);
            payload_json["resolved"]["style"]["axis_y"]["tick_values"] = ticks;
            payload_json["resolved"]["style"]["axis_y"]["domain_max"] = json!(dmax);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            let rects = parse_rects(&svg);
            let top = rects.iter().map(|r| r.1).fold(f64::MAX, f64::min);
            let bottom = rects.iter().map(|r| r.1 + r.3).fold(f64::MIN, f64::max);
            (top, bottom - top)
        };
        // Domain [−10, 20]: B reaches the domain max, A the floor — the bars
        // span the whole plot height.
        let (top_neg, plot_h_neg) = plot_rect(
            json!([{"product": "A", "revenue": -10.0}, {"product": "B", "revenue": 20.0}]),
            json!([-10.0, 0.0, 10.0, 20.0]),
            20.0,
        );
        // Domain [0, 30], the same ladder shifted up by 10. B stays off an
        // exact zero, which would add the genuine-zero label's ink.
        let (top_pos, plot_h_pos) = plot_rect(
            json!([{"product": "A", "revenue": 30.0}, {"product": "B", "revenue": 1.0}]),
            json!([0.0, 10.0, 20.0, 30.0]),
            30.0,
        );
        assert!((top_neg - top_pos).abs() < 0.01, "same top margin: {top_neg} vs {top_pos}");
        assert!(
            (plot_h_neg - plot_h_pos).abs() < 0.01,
            "same negotiated plot height: {plot_h_neg} vs {plot_h_pos}"
        );
    }

    /// `validTicks` (`vega.js:14547-14572`) drops every baked rung whose
    /// position falls outside the scale: a ladder rounded outward past the
    /// authored pair draws exactly what the ladder truncated at the pair
    /// draws — the dropped rung reserves no label ink either, so the whole
    /// render is identical.
    #[test]
    fn authored_domain_drops_baked_rungs_outside_it() {
        for horizontal in [false, true] {
            let render_with_ladder = |ladder: serde_json::Value| {
                let mut payload_json = make_authored_domain_payload(horizontal);
                payload_json["resolved"]["style"]["axis_y"]["scale"] =
                    serde_json::json!({"continuous": {"domain": [0.0, 3500.0]}});
                payload_json["resolved"]["style"]["axis_y"]["tick_values"] = ladder;
                render(&make_payload(payload_json), &test_fonts()).expect("renders")
            };
            let outward = render_with_ladder(serde_json::json!([0.0, 1000.0, 2000.0, 3000.0, 4000.0]));
            let truncated = render_with_ladder(serde_json::json!([0.0, 1000.0, 2000.0, 3000.0]));
            assert_eq!(outward, truncated, "horizontal={horizontal}");
            assert_eq!(value_axis_labels(&outward), ["0", "1,000", "2,000", "3,000"]);
        }
    }

    /// With no baked ladder and no authored count, Vega-Lite's default
    /// `tickCount` (`ceil(range/40)`) draws d3's ticks over the authored
    /// pair — including its negative half — once the plot extent is known
    /// (`unbaked_rungs` re-derives them from the domain floor, not from 0).
    #[test]
    fn authored_negative_domain_without_a_ladder_or_count_draws_ticks_from_its_floor() {
        for horizontal in [false, true] {
            let mut payload_json = make_authored_domain_payload(horizontal);
            payload_json["resolved"]["style"]["axis_y"]["scale"] =
                serde_json::json!({"continuous": {"domain": [-100.0, 100.0]}});
            payload_json["rows"][0]["revenue"] = serde_json::json!(32.0);
            payload_json["rows"][1]["revenue"] = serde_json::json!(54.0);
            payload_json["resolved"]["style"]["axis_y"]["tick_values"] = serde_json::json!([]);
            payload_json["resolved"]["style"]["axis_y"]["ticks"]["count"] = serde_json::json!(null);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            let labels = value_axis_labels(&svg);
            assert_eq!(labels.first().map(String::as_str), Some("\u{2212}100"), "horizontal={horizontal}: {labels:?}");
            assert_eq!(labels.last().map(String::as_str), Some("100"), "horizontal={horizontal}: {labels:?}");
            assert!(labels.contains(&"0".to_string()), "horizontal={horizontal}: {labels:?}");
        }
    }

    /// NUMERIC STRINGS keep the band arm too — production's
    /// `is_vega_numeric_value` deliberately rejects them (a string-typed
    /// numeric column compiles a NOMINAL scale), and a "NaN" cell must
    /// route to legacy rather than reach the linear arm's sort.
    #[test]
    fn numeric_string_x_keeps_the_band_arm() {
        let mut payload_json = make_continuous_payload(&[(0.0, 100.0), (1.0, 200.0)]);
        payload_json["resolved"]["style"]["mark"].as_object_mut().unwrap().remove("max_size");
        payload_json["rows"] = serde_json::json!([
            {"product": "0", "revenue": 100.0},
            {"product": "1", "revenue": 200.0},
            {"product": "NaN", "revenue": 300.0},
        ]);
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("string x renders on the band arm");
        assert!(svg.contains(">0</text>") && svg.contains(">NaN</text>"), "band labels:
{svg}");
    }

    /// A string x keeps the band arm (the gate sniffs every row).
    #[test]
    fn string_x_keeps_the_band_arm() {
        let payload_json = make_vertical_payload(&[("Alpha", 1000.0), ("Beta", 500.0)], false);
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("band renders");
        assert!(svg.contains(">Alpha</text>"), "band label per category:\n{svg}");
    }

    fn support_table_style_json() -> serde_json::Value {
        serde_json::json!({
            "font": {"family": TEST_FONT, "size": 11.0, "color": "#6F685D"},
            "divider": {"gap": 4.0, "visible": false, "color": "#626366", "width": 1.0, "dasharray": null},
            "row": {"padding": {"vertical": 2.0, "horizontal": 4.0},
                    "divider": {"visible": false, "color": "#626366", "width": 1.0, "dasharray": null}},
            "label": {"padding": 8.0, "font": {"family": TEST_FONT, "size": 11.0, "color": "#6F685D"}},
            "padding_top": 8.0,
            "padding_bottom": 4.0,
            "label_max_lines": 2,
            "position": "top",
        })
    }

    /// `make_vertical_payload`'s axis is LEFT-oriented — `composed_cfg` now
    /// covers either axis side (probed: `gs._prod_render`, a titled
    /// single-series left-axis bar; production's plot stays at its declared
    /// height and `plot_left = pad_l + max(the axis's own label gutter, the
    /// stub's reach)`, the mirror of the right-axis formula). Mutation
    /// counterfactual: reverting the left-axis branch to the right-axis one
    /// (`zero_ink.max(cell_left_ink)`) drops `plot_left` to `pad_l + 2` and
    /// fails the assertion below.
    #[test]
    fn attached_support_table_grows_the_card_and_renders_the_strip() {
        let mut payload_json = make_vertical_payload(
            &[("Alpha", 1000.0), ("Beta", 2000.0)],
            false,
        );
        payload_json["resolved"]["support_table"] = serde_json::json!({
            "entries": [{"source": "revenue", "format": {"spec": "$,.0f", "raw": "currency_whole"}, "label": "Revenue"}]
        });
        payload_json["resolved"]["effective_support_table_style"] = support_table_style_json();
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("left-axis composed strip renders");

        let plot_top: f64 = svg
            .split("<g transform=\"translate(")
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
        // Same plot_top law as the right-axis algebra test, axis-independent:
        // pad_t(16) + title_fs(18) + title_offset_theme(10) +
        // correction(18)=7 + ink_ext(19) = 70.
        assert!((plot_top - 70.0).abs() < 0.01, "plot_top is axis-independent: {plot_top}\n{svg}");

        let plot_left: f64 = svg
            .split("<g transform=\"translate(")
            .nth(1)
            .unwrap()
            .split(',')
            .next()
            .unwrap()
            .parse()
            .unwrap();
        // gutter_of's left-axis branch: labels.padding(6) + widest tick label.
        let val_labels = ["0", "500", "1,000", "1,500", "2,000"];
        let max_val_w = val_labels
            .iter()
            .map(|s| fonts.measure(s, TEST_FONT, 11.0).expect("test font measures"))
            .fold(0.0_f64, f64::max);
        let val_gutter = 6.0 + max_val_w;
        // The stub's own reach: labels.padding(6) + "Revenue"'s width.
        // The stub's own width is shaped (`build_rows_composed`'s
        // `measure_shaped`, GPOS kerning), unlike the axis tick labels above.
        let stub_w = fonts.measure_shaped("Revenue", TEST_FONT, 11.0).expect("test font measures");
        let stub_reach = 6.0 + stub_w;
        // Vega's own `autosize: pad` negotiation snaps the view's origin to
        // a whole pixel (`docs/laws-support-table.md`'s plot-origin note) —
        // every probed row's `translate(px,py)` role-frame origin is an
        // integer, never a fraction.
        let expected_left = (16.0 + val_gutter.max(stub_reach).max(2.0)).ceil();
        assert!(
            (plot_left - expected_left).abs() < 0.01,
            "plot_left = pad_l + max(val_gutter, stub_reach, zero_ink), whole-pixel snapped: \
             got {plot_left}, expected {expected_left}\n{svg}"
        );
    }

    /// HIGH, historically: a `position: top` strip on a grouped bar with a
    /// top legend must not paint over the legend. `composed_cfg` now covers
    /// a grouped bar's rail-disabled/legend-suppressed shape (this fixture's
    /// own `false`-legend sibling), but a chart that actually KEEPS its
    /// legend still refuses: the legend's own box under the composed pad
    /// arm has no probed margin term yet. Pinned as a refusal instead of a
    /// wrong render.
    #[test]
    fn attached_support_table_top_strip_does_not_overlap_a_top_legend() {
        let mut payload_json =
            make_vertical_payload(&[("Alpha", 1000.0), ("Beta", 2000.0)], true);
        payload_json["resolved"]["support_table"] = serde_json::json!({
            "entries": [{"source": "revenue", "aggregate": "sum", "format": {"spec": "$,.0f", "raw": "currency_whole"}, "label": "Revenue"}]
        });
        payload_json["resolved"]["effective_support_table_style"] = support_table_style_json();
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("a kept top legend takes the pad arm");
        assert!(svg.contains(">Revenue<") && svg.contains(">Alpha<"), "{svg}");
    }

    /// Same shape, `legend.position: right`.
    #[test]
    fn attached_support_table_top_strip_shifts_with_a_right_legend() {
        let mut payload_json =
            make_vertical_payload(&[("Alpha", 1000.0), ("Beta", 2000.0)], true);
        payload_json["resolved"]["legend"]["position"] = serde_json::json!({"edge": "right", "align": "start", "overlay": false});
        payload_json["resolved"]["legend"]["direction"] = serde_json::json!("vertical");
        payload_json["resolved"]["support_table"] = serde_json::json!({
            "entries": [{"source": "revenue", "aggregate": "sum", "format": {"spec": "$,.0f", "raw": "currency_whole"}, "label": "Revenue"}]
        });
        payload_json["resolved"]["effective_support_table_style"] = support_table_style_json();
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("a kept right legend takes the pad arm");
        assert!(svg.contains(">Revenue<") && svg.contains(">Alpha<"), "{svg}");
    }

    /// Regression: the strip used to rebuild its own plot
    /// box (`plot_left = pad_l`, evenly-spaced columns) instead of using
    /// `render_vertical`'s real one — drifting rightward whenever wide value
    /// ticks or a right legend grow the margins. A right-side value axis
    /// with wide tick labels grows `val_margin` well past the naive
    /// `pad_l`/evenly-spaced assumption; the strip's cells must still land
    /// exactly on the bars' own band centers.
    #[test]
    fn attached_support_table_cells_align_with_band_centers_when_margins_widen() {
        let mut payload_json =
            make_vertical_payload(&[("Alpha", 1_000_000.0), ("Beta", 2_000_000.0)], false);
        payload_json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("right");
        payload_json["resolved"]["style"]["axis_y"]["tick_values"] =
            serde_json::json!([0.0, 1_000_000.0, 2_000_000.0]);
        payload_json["resolved"]["style"]["mark"]["border"]["radius"] = serde_json::json!(0.0);
        payload_json["resolved"]["support_table"] = serde_json::json!({
            "entries": [{"source": "revenue", "format": {"spec": "$,.0f", "raw": "currency_whole"}, "label": "Revenue"}]
        });
        payload_json["resolved"]["effective_support_table_style"] = support_table_style_json();
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("support_table bar renders");

        // Bar rects: <rect x="..." width="..." ...> inside the plot group.
        let bar_centers: Vec<f64> = svg
            .match_indices("<rect x=\"")
            .map(|(start, _)| {
                let snippet = &svg[start..start + 200];
                let x: f64 = snippet.split("x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
                let w: f64 =
                    snippet.split("width=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
                x + w / 2.0
            })
            .collect();
        assert_eq!(bar_centers.len(), 2, "two bars drawn: {svg}");

        // Plot group's own x translate is the real plot_left; bar rect x is
        // plot-local, so absolute bar-center x = plot_left + local center.
        // The strip's own wrapping `<g transform="translate(0, ...)">`
        // (support_table_strip::wrap_with_strip) writes a comma-space; the
        // plot's own group (this family's own emission) never does —
        // distinguish on that rather than matching the first occurrence.
        let tag = svg
            .match_indices("<g transform=\"translate(")
            .map(|(idx, _)| &svg[idx..idx + 60])
            .find(|tag| !tag.contains(", "))
            .expect("plot group present");
        let plot_left: f64 =
            tag.split("translate(").nth(1).unwrap().split(',').next().unwrap().parse().unwrap();
        let absolute_bar_centers: Vec<f64> = bar_centers.iter().map(|c| plot_left + c).collect();

        // This fixture takes the composed autosize-pad arm: production
        // paints cells right-aligned at `band_center + dx` (one shared
        // lane, dx = half the widest BARE cell), and the trimmed currency
        // row declares its `$` once on the anchor cell — the second cell
        // paints bare digits.
        let x_of_text = |needle: &str| -> f64 {
            let pos = svg.find(needle).unwrap_or_else(|| panic!("{needle} missing: {svg}"));
            let tag_start = svg[..pos].rfind("<text").unwrap();
            svg[tag_start..pos].split("x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        let cell_xs: Vec<f64> = vec![x_of_text(">$1,000,000<"), x_of_text(">2,000,000<")];

        let lane_offsets: Vec<f64> = cell_xs
            .iter()
            .zip(absolute_bar_centers.iter())
            .map(|(cell_x, bar_x)| cell_x - bar_x)
            .collect();
        assert!(
            (lane_offsets[0] - lane_offsets[1]).abs() < 0.01,
            "both cells share one band-centered lane (equal dx): {lane_offsets:?}\n{svg}"
        );
        let bare_w = fonts
            .measure("2,000,000", "'Inter'", 11.0)
            .expect("test font measures");
        assert!(
            (lane_offsets[0] - bare_w / 2.0).abs() < 0.5,
            "dx is half the widest bare cell ({}): got {lane_offsets:?}\n{svg}",
            bare_w / 2.0
        );
    }

    /// The composed autosize-pad algebra, pinned literally (fixture: 800×500
    /// view, pads 16, title fs 18 offset 10; strip style pad_top 8 /
    /// pad_bottom 4 / row_pad 2 → row_h 18):
    ///   strip ink extent = (4+18+9)+6 = 37; correction(18) = 11-18+14 = 7;
    ///   plot_top = 16+18+10+7+37 = 88; plot_left = 16+2 (zero-rule ink);
    ///   canvas_h = 500+⌈88+0+6+11+16⌉ = 621; title baseline = 16+14;
    ///   row baselines = plot_top − (4+9) + 3 and − (4+18+9) + 3.
    #[test]
    fn a_strip_entry_declares_its_unit_once_from_the_wire_raw_name() {
        let render_entry = |format: serde_json::Value| {
            let mut payload_json =
                make_vertical_payload(&[("Alpha", 1000.0), ("Beta", 2000.0)], false);
            payload_json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("right");
            payload_json["resolved"]["support_table"] = serde_json::json!({
                "entries": [{"source": "revenue", "format": format, "label": "Revenue"}]
            });
            payload_json["resolved"]["effective_support_table_style"] = support_table_style_json();
            render(&make_payload(payload_json), &test_fonts()).expect("composed strip renders")
        };
        let by_name = render_entry(serde_json::json!({"spec": "$,.0f", "raw": "currency_whole"}));
        // The same spec authored as a d3 literal, or through a user alias,
        // repeats the `$` on every cell.
        let literal = render_entry(serde_json::json!({"spec": "$,.0f", "raw": "$,.0f"}));
        assert_ne!(literal, by_name);
        assert_eq!(render_entry(serde_json::json!({"spec": "$,.0f", "raw": "my_money"})), literal);
    }

    #[test]
    fn composed_strip_matches_the_autosize_pad_algebra() {
        let mut payload_json =
            make_vertical_payload(&[("Alpha", 1000.0), ("Beta", 2000.0)], false);
        payload_json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("right");
        payload_json["resolved"]["support_table"] = serde_json::json!({
            "entries": [
                {"source": "revenue", "format": {"spec": "$,.0f", "raw": "currency_whole"}, "label": "Revenue"},
                {"source": "revenue", "format": ",d", "label": "Units"}
            ]
        });
        payload_json["resolved"]["effective_support_table_style"] = support_table_style_json();
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("composed strip renders");

        assert!(svg.contains("height=\"621\""), "canvas height 621: {svg}");
        assert!(
            svg.contains("<g transform=\"translate(18.00,88.00)\">"),
            "plot origin (18, 88): {svg}"
        );
        // Title anchors to the padding edge: baseline pad_t + shift("top", 18).
        let title_pos = svg.find(">Test Vertical Bar<").expect("title present");
        let title_tag = &svg[svg[..title_pos].rfind("<text").unwrap()..title_pos];
        assert!(
            title_tag.contains("x=\"16.00\" y=\"30.00\""),
            "title baseline at (16, 30): {title_tag}"
        );
        // Declare-once currency: the anchor keeps the `$`, its sibling is bare.
        assert!(svg.contains(">$1,000<"), "anchor cell declares the $: {svg}");
        assert!(svg.contains(">2,000<"), "non-anchor cell is bare: {svg}");
        assert!(!svg.contains(">$2,000<"), "the $ never repeats: {svg}");
        // Row baselines: row 0 (closest to the plot) at 88−13+3, row 1 at 88−31+3.
        for (needle, y) in [(">Revenue<", "78.00"), (">Units<", "60.00")] {
            let pos = svg.find(needle).unwrap_or_else(|| panic!("{needle} missing: {svg}"));
            let tag = &svg[svg[..pos].rfind("<text").unwrap()..pos];
            assert!(
                tag.contains(&format!("y=\"{y}\"")) && tag.contains("x=\"824.00\""),
                "stub {needle} at (824, {y}): {tag}"
            );
        }
    }

    /// `autosize: pad` keeps the spec's own height verbatim and adds each
    /// margin as an integer (`ViewLayout.js:198-201`: `viewHeight = height +
    /// top + bottom`, `top`/`bottom` ceil'd at `:165-168`), so a fractional
    /// plot height gives a fractional canvas. Production's width correction
    /// hands the support-table height pass exactly this: `ladder/sizing/13`
    /// renders `bar_with_table` at a 401.3333 spec height and reads a canvas
    /// of 401.3333 + 101 (`layout_sizing.py::_correct_support_table_height`;
    /// `extract_svg_dimensions` reads the viewBox unrounded). This fixture's
    /// margins are 70 above and 33 below the plot, so the canvas is 401.3333 + 103.
    #[test]
    fn composed_strip_keeps_a_fractional_plot_height_in_the_canvas() {
        let mut payload_json =
            make_vertical_payload(&[("Alpha", 1000.0), ("Beta", 2000.0)], false);
        payload_json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("right");
        payload_json["resolved"]["support_table"] = serde_json::json!({
            "entries": [{"source": "revenue", "format": ",d", "label": "Units"}]
        });
        payload_json["resolved"]["effective_support_table_style"] = support_table_style_json();
        let plot_h = 401.0 + 1.0 / 3.0;
        payload_json["height"] = serde_json::json!(plot_h);
        let svg = render(&make_payload(payload_json), &test_fonts())
            .expect("composed strip renders");
        let canvas_h: f64 = svg
            .split("height=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
        assert!(
            (canvas_h - (plot_h + 103.0)).abs() < 1e-9,
            "canvas {canvas_h} is the spec height plus integer margins: {svg}"
        );
    }

    /// `top` and `bottom` are ceil'd separately (`ViewLayout.js:165`, `:168`)
    /// and summed with the verbatim height (`:198-201`), so two fractional
    /// margins each round up on their own: 70.1 and 33.4 give 71 + 34, where
    /// ceiling their sum (103.5) would stop at 104. The same ceil'd top
    /// margin is the plot group's origin, so the origin is the whole pixel 71
    /// the canvas reserves.
    #[test]
    fn composed_strip_ceils_each_fractional_margin_on_its_own() {
        let mut payload_json =
            make_vertical_payload(&[("Alpha", 1000.0), ("Beta", 2000.0)], false);
        payload_json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("right");
        payload_json["resolved"]["style"]["axis_x"]["labels"]["padding"] = serde_json::json!(6.4);
        payload_json["resolved"]["support_table"] = serde_json::json!({
            "entries": [{"source": "revenue", "format": ",d", "label": "Units"}]
        });
        let mut strip_style = support_table_style_json();
        strip_style["row"]["padding"]["vertical"] = serde_json::json!(2.1);
        payload_json["resolved"]["effective_support_table_style"] = strip_style;
        payload_json["height"] = serde_json::json!(500.0);
        let svg = render(&make_payload(payload_json), &test_fonts())
            .expect("composed strip renders");
        assert!(svg.contains("<g transform=\"translate(18.00,71.00)\">"), "plot top ceil(70.1): {svg}");
        assert!(svg.contains("height=\"605\""), "500 + 71 + ceil(33.4): {svg}");
    }

    /// Untitled, the strip's height rides the top padding instead of the title
    /// offset, outside the ceil: the origin is `pad_t + strip_h +
    /// ceil(ink_ext)` and keeps the strip's fractional height (`weekly_bar`
    /// untitled at `row.padding.vertical` 2.1, 2.35 and 2.7: origin 138.6,
    /// 141.1 and 145.2, canvas 446.6, 449.1 and 453.2 at spec height 261).
    /// The canvas adds the plot height and the ceil'd bottom margin to it.
    #[test]
    fn composed_strip_untitled_origin_keeps_the_strip_fractional() {
        for (vertical, origin) in [(2.1, 66.2), (2.35, 66.7), (2.7, 67.4)] {
            let mut payload_json =
                make_vertical_payload(&[("Alpha", 1000.0), ("Beta", 2000.0)], false);
            payload_json["resolved"]["title"] = serde_json::json!(null);
            payload_json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("right");
            payload_json["resolved"]["support_table"] = serde_json::json!({
                "entries": [{"source": "revenue", "format": ",d", "label": "Units"}]
            });
            let mut strip_style = support_table_style_json();
            strip_style["row"]["padding"]["vertical"] = serde_json::json!(vertical);
            payload_json["resolved"]["effective_support_table_style"] = strip_style;
            payload_json["height"] = serde_json::json!(500.0);
            let svg = render(&make_payload(payload_json), &test_fonts())
                .expect("untitled composed strip renders");
            let group = svg.split("<g transform=\"translate(").nth(1).unwrap();
            let y = group.split(',').nth(1).unwrap().split(')').next().unwrap();
            let plot_top: f64 = y.parse().unwrap();
            assert!(
                (plot_top - origin).abs() < 1e-9,
                "vertical={vertical}: origin {plot_top}: {svg}"
            );
            let canvas_h: f64 = svg
                .split("height=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
            assert!(
                (canvas_h - (500.0 + plot_top + 33.0)).abs() < 1e-9,
                "vertical={vertical}: canvas = h + origin + ceil(bottom): {svg}"
            );
        }
    }

    /// Production reserves the strip's height in `padding.top` when the chart
    /// has no title to hang `title.offset` on
    /// (`apply_chart_support_table_post_pass`: `has_title` picks
    /// `title.offset`, every other `position: top` branch bumps the padding —
    /// VL emits no `role-title-text` for an offset to anchor on). Same
    /// fixture as the algebra test above, untitled: plot_top = pad_t(16) +
    /// strip_h(8 + 18 + 4) + ink extent(13 + 9 - 3) = 65, and the canvas
    /// grows with it.
    #[test]
    fn composed_strip_untitled_reserves_the_strip_in_the_top_padding() {
        let mut payload_json =
            make_vertical_payload(&[("Alpha", 1000.0), ("Beta", 2000.0)], false);
        payload_json["resolved"]["title"] = serde_json::json!(null);
        payload_json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("right");
        payload_json["resolved"]["support_table"] = serde_json::json!({
            "entries": [{"source": "revenue", "format": {"spec": "$,.0f", "raw": "currency_whole"}, "label": "Revenue"}]
        });
        payload_json["resolved"]["effective_support_table_style"] = support_table_style_json();
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("untitled composed strip renders");

        assert!(
            svg.contains("<g transform=\"translate(18.00,65.00)\">"),
            "untitled plot top = pad_t + strip_h + ink extent: {svg}"
        );
        // Row 0's baseline follows the plot: 65 − 13 + 3.
        let pos = svg.find(">Revenue<").expect("stub present");
        let tag = &svg[svg[..pos].rfind("<text").unwrap()..pos];
        assert!(tag.contains("y=\"55.00\""), "stub baseline follows the plot: {tag}");
    }

    /// S6: a cell painted wider than its own lane's offset from the plot's
    /// left edge overhangs it, and that overhang joins the canvas union — so
    /// the overhanging cell's left edge lands exactly on the padding edge,
    /// the way `weekly_line`'s `$102,850` puts production's plot at
    /// `16 + 53`. Six categories on a 200-wide plot put band 0's center at
    /// ~17px, well inside the anchor cell's own width.
    #[test]
    fn composed_strip_first_cell_overhang_joins_the_canvas() {
        let cats: Vec<(&str, f64)> = vec![
            ("A", 102850.0),
            ("B", 97469.0),
            ("C", 86180.0),
            ("D", 111122.0),
            ("E", 45402.0),
            ("F", 60310.0),
        ];
        let mut payload_json = make_vertical_payload(&cats, false);
        payload_json["width"] = serde_json::json!(200.0);
        payload_json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("right");
        payload_json["resolved"]["support_table"] = serde_json::json!({
            "entries": [{"source": "revenue", "format": {"spec": "$,.0f", "raw": "currency_whole"}, "label": "Revenue"}]
        });
        payload_json["resolved"]["effective_support_table_style"] = support_table_style_json();
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("narrow composed strip renders");

        let pad_l = 16.0;
        let plot_left: f64 = svg
            .split("<g transform=\"translate(")
            .nth(1)
            .unwrap()
            .split(',')
            .next()
            .unwrap()
            .parse()
            .unwrap();
        // The zero rule's 2px stroke is the only other left-ink term on this
        // arm, so a plot past `pad_l + 2` means the cell is what moved it.
        assert!(
            plot_left > pad_l + 2.0,
            "the first cell must out-reach the zero rule for this test to mean anything: {plot_left}"
        );
        let cell = ">$102,850<";
        let pos = svg.find(cell).unwrap_or_else(|| panic!("anchor cell missing: {svg}"));
        let tag = &svg[svg[..pos].rfind("<text").unwrap()..pos];
        let cell_x: f64 =
            tag.split("x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
        let strip_font = "'Inter Variable', 'Noto Emoji', Inter, system-ui, sans-serif";
        let cell_w = fonts.measure_shaped("$102,850", strip_font, 11.0).expect("measures");
        // `cell.x` is laid out against the PRE-`.ceil()` `plot_left` (the
        // cell overhang IS `cell_left_ink`, by construction), so the cell's
        // own left edge lands AT OR PAST `pad_l` by exactly whatever
        // `plot_left`'s whole-pixel snap rounded up — never short of it,
        // never by more than 1px.
        let left_edge = cell_x - cell_w;
        assert!(
            left_edge >= pad_l - 0.01 && left_edge < pad_l + 1.0,
            "the overhanging cell's left edge lands within 1px short of the padding \
             edge, the `plot_left` whole-pixel snap's own residual: \
             {cell_x} - {cell_w} vs {pad_l}\n{svg}"
        );
    }

    /// Production's provenance gate (`_entry_numerals`): a raw d3 literal —
    /// even one spelling the exact spec a predefined name resolves to —
    /// never anchors; every cell keeps its own affix.
    #[test]
    fn composed_strip_raw_literal_format_keeps_per_cell_affixes() {
        let mut payload_json =
            make_vertical_payload(&[("Alpha", 1000.0), ("Beta", 2000.0)], false);
        payload_json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("right");
        payload_json["resolved"]["support_table"] = serde_json::json!({
            "entries": [{"source": "revenue", "format": "$,.0f", "label": "Raw"}]
        });
        payload_json["resolved"]["effective_support_table_style"] = support_table_style_json();
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("raw-literal strip renders");
        assert!(svg.contains(">$1,000<"), "first cell keeps its $: {svg}");
        assert!(svg.contains(">$2,000<"), "second cell keeps its $ too: {svg}");
    }

    fn support_table_style_json_bottom() -> serde_json::Value {
        let mut v = support_table_style_json();
        v["position"] = serde_json::json!("bottom");
        v
    }

    /// A support table leaves the chart's root encoding in place, so production
    /// still judges the bands.
    #[test]
    fn a_vertical_bar_with_a_support_table_is_still_judged_for_band_width() {
        let cats: Vec<(String, f64)> = (0..40).map(|i| (format!("c{i:02}"), 1000.0 + i as f64)).collect();
        let cats: Vec<(&str, f64)> = cats.iter().map(|(c, v)| (c.as_str(), *v)).collect();
        let mut p = bottom_strip_payload();
        p["rows"] = make_vertical_payload(&cats, false)["rows"].clone();
        p["width"] = serde_json::json!(120.0);
        let (svg, warnings) = crate::diagnostic::collect(|| render(&make_payload(p), &test_fonts()));
        svg.expect("renders");
        let w = warnings.iter().find(|d| d.code == "WARN-BAR-BAND-WIDTH-TOO-NARROW").expect("warns");
        assert_eq!(serde_json::to_value(&w.params).unwrap()["distinct"], serde_json::json!(40));
    }

    /// An authored tilt grows a bottom strip's axis gap for text categories, but
    /// production's pinned-angle block height is the font size on a numeric x.
    #[test]
    fn an_authored_tilt_grows_the_bottom_gap_for_text_categories_not_numeric_ones() {
        let cell_y = |numeric: bool, angle: f64| -> f64 {
            let mut p = bottom_strip_payload();
            if numeric {
                p["rows"][0]["product"] = serde_json::json!(7001);
                p["rows"][1]["product"] = serde_json::json!(7002);
            } else {
                p["rows"][0]["product"] = serde_json::json!("A rather long category name");
            }
            p["resolved"]["style"]["axis_x"]["labels"]["angle"] = serde_json::json!(angle);
            let svg = render(&make_payload(p), &test_fonts()).expect("renders");
            let i = svg.find(">$1,000<").unwrap();
            let tag = &svg[svg[..i].rfind("<text").unwrap()..i];
            tag.split(" y=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        assert!(cell_y(false, -45.0) > cell_y(false, 0.0), "text categories grow the gap");
        assert_eq!(cell_y(true, -45.0), cell_y(true, 0.0), "a numeric x keeps the baked gap");
    }

    fn bottom_strip_payload() -> serde_json::Value {
        let mut payload_json = make_vertical_payload(&[("Alpha", 1000.0), ("Beta", 2000.0)], false);
        payload_json["resolved"]["support_table"] = serde_json::json!({
            "entries": [{"source": "revenue", "format": {"spec": "$,.0f", "raw": "currency_whole"}, "label": "Revenue"}]
        });
        payload_json["resolved"]["effective_support_table_style"] = support_table_style_json_bottom();
        payload_json["resolved"]["support_table_axis_offset"] = serde_json::json!(17.0);
        payload_json["resolved"]["title"] = serde_json::Value::Null;
        payload_json["resolved"]["subtitle"] = serde_json::Value::Null;
        payload_json
    }

    /// Vega draws no label marks for `labels: false`, so no category text sits
    /// between the plot and the strip.
    #[test]
    fn hidden_x_labels_under_a_bottom_strip_paint_no_category_text() {
        let mut p = bottom_strip_payload();
        let shown = render(&make_payload(p.clone()), &test_fonts()).expect("renders");
        assert!(shown.contains(">Alpha<") && shown.contains(">Beta<"), "control: {shown}");
        p["resolved"]["style"]["axis_x"]["labels"]["visible"] = serde_json::json!(false);
        let hidden = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(!hidden.contains(">Alpha<") && !hidden.contains(">Beta<"), "{hidden}");
        assert!(hidden.contains(">$1,000<"), "the strip still paints: {hidden}");
    }

    /// `labels: false` draws no label marks at all, so a date x paints neither
    /// of its two label rows.
    #[test]
    fn a_date_x_with_hidden_labels_draws_neither_label_row() {
        let mut p = make_vertical_payload(
            &[("2024-01-01", 100.0), ("2024-02-01", 150.0), ("2024-03-01", 120.0)],
            false,
        );
        let shown = render(&make_payload(p.clone()), &test_fonts()).expect("renders");
        assert!(shown.contains(">Jan<") && shown.contains(">2024<"), "control: {shown}");
        p["resolved"]["style"]["axis_x"]["labels"]["visible"] = serde_json::json!(false);
        let hidden = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(!hidden.contains(">Jan<") && !hidden.contains(">2024<"), "{hidden}");
    }

    /// A date axis's label layout (cadence, tilt) sizes the strip's axis gap.
    #[test]
    fn a_bottom_strip_under_date_x_without_an_authored_angle_renders() {
        let mut p = bottom_strip_payload();
        p["rows"][0]["product"] = serde_json::json!("2024-01-01");
        p["rows"][1]["product"] = serde_json::json!("2024-02-01");
        let svg = render(&make_payload(p), &test_fonts()).expect("a date axis takes a bottom strip");
        assert!(svg.contains(">Revenue<"), "{svg}");
    }

    /// Production keeps a bottom strip's plot at its declared height and grows
    /// the canvas under the axis (`padding.bottom` carries the strip and the
    /// axis gap).
    #[test]
    fn a_bottom_support_table_strip_keeps_the_plot_height_and_grows_the_canvas() {
        let payload_json = bottom_strip_payload();
        let h = payload_json["height"].as_f64().unwrap();
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("a bottom strip renders");
        let canvas_h: f64 = svg.split("height=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
        assert!(canvas_h >= h + 17.0 + 30.0, "axis gap and strip are added under the declared height: {canvas_h} vs {h}");
        assert!(svg.contains(">$1,000<") && svg.contains(">Revenue<"), "{svg}");
    }

    /// The row's `y` moves one-for-one with the baked axis gap, and a stub label
    /// set larger than the cells sets the ink the canvas clears.
    #[test]
    fn a_bottom_strip_row_follows_the_axis_gap_and_clears_its_stub_ink() {
        let cell_y = |svg: &str| -> f64 {
            let i = svg.find(">$1,000<").unwrap();
            let tag = &svg[svg[..i].rfind("<text").unwrap()..i];
            tag.split(" y=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        let canvas_h = |svg: &str| -> f64 { svg.split("height=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap() };
        let render_with = |gap: f64, label_size: f64| {
            let mut p = bottom_strip_payload();
            p["resolved"]["support_table_axis_offset"] = serde_json::json!(gap);
            p["resolved"]["effective_support_table_style"]["label"]["font"]["size"] = serde_json::json!(label_size);
            render(&make_payload(p), &test_fonts()).expect("renders")
        };
        let (near, far) = (render_with(17.0, 11.0), render_with(27.0, 11.0));
        assert!((cell_y(&far) - cell_y(&near) - 10.0).abs() < 0.01, "{} vs {}", cell_y(&far), cell_y(&near));
        assert!(canvas_h(&render_with(17.0, 40.0)) > canvas_h(&near), "a taller stub label reaches lower");
    }

    #[test]
    fn a_top_strip_under_a_subtitle_renders_both_title_rows() {
        let mut p = bottom_strip_payload();
        p["resolved"]["effective_support_table_style"] = support_table_style_json();
        p["resolved"]["title"] = serde_json::json!("A title");
        p["resolved"]["subtitle"] = serde_json::json!("A subtitle");
        let svg = render(&make_payload(p), &test_fonts()).expect("a subtitle sits above a top strip");
        assert!(svg.contains(">A title<") && svg.contains(">A subtitle<"), "{svg}");
    }

    #[test]
    fn a_top_strip_under_a_wrapped_title_renders() {
        let mut p = bottom_strip_payload();
        p["resolved"]["effective_support_table_style"] = support_table_style_json();
        p["width"] = serde_json::json!(240.0);
        p["resolved"]["title"] = serde_json::json!(
            "A very long chart title that cannot fit on one line of this narrow chart and so must wrap"
        );
        let svg = render(&make_payload(p), &test_fonts()).expect("a wrapped title sits above a top strip");
        assert!(svg.contains(">Revenue<"), "{svg}");
    }

    /// A titled chart keeps its title where an unstripped pad chart puts it;
    /// the bottom strip still only adds under the plot.
    #[test]
    fn a_titled_bottom_support_table_strip_keeps_the_title_above_the_plot() {
        let mut payload_json = bottom_strip_payload();
        let untitled = render(&make_payload(payload_json.clone()), &test_fonts()).expect("untitled renders");
        payload_json["resolved"]["title"] = serde_json::json!("A title");
        payload_json["resolved"]["subtitle"] = serde_json::json!("A subtitle");
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("a titled bottom strip renders");
        assert!(svg.contains(">A subtitle<"), "{svg}");
        let height = |svg: &str| -> f64 { svg.split("height=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap() };
        assert!(svg.contains(">A title<"), "{svg}");
        assert!(height(&svg) > height(&untitled), "the title adds a band above the plot");
    }

    #[test]
    fn vertical_bar_renders_without_error() {
        let payload_json = make_vertical_payload(
            &[("Alpha", 1800.0), ("Beta", 1200.0), ("Gamma", 900.0)],
            false,
        );
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("vertical bar renders");
        assert!(svg.contains("<svg"), "output is SVG");
        assert!(svg.contains("Test Vertical Bar"), "title present");
    }

    #[test]
    fn vertical_bars_grow_upward_from_baseline() {
        // Single-series with one category and radius=0 (→ <rect> mark, easier to parse).
        // The bar's y (top) must be above the baseline and bar height must be positive.
        use serde_json::json;
        let mut payload_json = make_vertical_payload(&[("Alpha", 1000.0)], false);
        // Force radius to 0 so the mark is a plain <rect>, not a <path>.
        payload_json["resolved"]["style"]["mark"]["border"]["radius"] = json!(0.0);

        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        // The first <rect x= after the background rect is the bar.
        // Background: <rect width= height= fill=  (no x= attribute).
        // Find bar rect — the one with an x= attribute in the plot group.
        let all_rects: Vec<_> = svg.match_indices("<rect x=").collect();
        assert!(!all_rects.is_empty(), "at least one bar rect present in SVG");

        // Parse the first bar rect's y and height.
        let (start, _) = all_rects[0];
        let snippet = &svg[start..start + 300];

        let y_val: f64 = snippet
            .split("y=\"")
            .nth(1).unwrap()
            .split('"').next().unwrap()
            .parse().unwrap();
        let h_val: f64 = snippet
            .split("height=\"")
            .nth(1).unwrap()
            .split('"').next().unwrap()
            .parse().unwrap();

        // Bar top (y_val in plot coords) must be non-negative and bar must have height.
        assert!(y_val >= 0.0, "bar top ≥ 0: {y_val}");
        assert!(h_val > 0.0, "bar height > 0: {h_val}");
        // In SVG: y is the top edge; y + h is the bottom (baseline).
        // For a column bar, y < y + h i.e. bar grows downward to baseline.
        assert!(y_val < y_val + h_val, "bar has positive extent (grows to baseline)");
    }

    /// A rendered vertical bar's right-side geometry: the plot rect's right
    /// margin (the canvas width less the 16px card padding, the plot group's
    /// `translate` x and the zero rule's `x2`, which spans exactly
    /// `[0, plot_w]`), the plot width, and the right edge of the rightmost
    /// bar rect inside the plot.
    fn plot_right_geometry(svg: &str) -> (f64, f64, f64) {
        let attr = |tag: &str, name: &str| -> f64 {
            let at = tag.find(&format!(" {name}=\"")).expect("attribute") + name.len() + 3;
            tag[at..].split('"').next().unwrap().parse().expect("numeric attribute")
        };
        let open = svg.find("<g transform=\"translate(").expect("plot group");
        let tx: f64 = svg[open + "<g transform=\"translate(".len()..]
            .split(',')
            .next()
            .unwrap()
            .parse()
            .expect("numeric translate");
        let rule_end = svg.find("stroke=\"#8A8C8F\"").expect("zero rule");
        let plot = &svg[open..rule_end];
        let plot_w = attr(&plot[plot.rfind("<line").unwrap()..], "x2");
        let bars_right = plot
            .match_indices("<rect x=")
            .map(|(at, _)| attr(&plot[at..], "x") + attr(&plot[at..], "width"))
            .fold(0.0, f64::max);
        (800.0 - 16.0 - (tx + plot_w), plot_w, bars_right)
    }

    /// A single vertical bar whose outer rects sit flush on the plot edge
    /// (`band_width: 1`) crosses it by its own border stroke: Vega's rect
    /// bound is stroke-expanded on every side (`vega.js:15959-15965`,
    /// `boundStroke`, the full width), so the fit reserves the overhang on
    /// the right, where no axis ink covers it. The zero rule's own 2px
    /// expansion is the floor (`zero_w`), so a 1px stroke reserves nothing
    /// extra and a default-inset band (`0.7`, 36px of padding) never
    /// reaches the edge. An inset band absorbs `(1 - band) / 2` of a band of
    /// the stroke: six 121px bands at `band: 0.95` inset 3px, so a 6px
    /// stroke reserves `ceil(6 - 3) = 3`, and at `band: 0.9` a 10px stroke
    /// reserves `ceil(10 - 6) = 4`. (The probe pins 4 and 6 left / 5 right
    /// for 100px bands; the left of that asymmetric pair has no analog
    /// here, where the value axis covers the left edge.)
    /// `libs/chart-svg/tests/test_vl_probe.py`'s
    /// `test_a_bar_rect_bound_reserves_its_full_stroke_past_the_plot_edge`
    /// pins the same law against Vega.
    #[test]
    fn a_full_band_stroke_reserves_its_overhang_in_the_fit() {
        use serde_json::json;
        let cats = [("A", 1800.0), ("B", 1200.0), ("C", 900.0)];
        let six = [
            ("A", 1800.0),
            ("B", 1200.0),
            ("C", 900.0),
            ("D", 1500.0),
            ("E", 700.0),
            ("F", 1100.0),
        ];
        for (cats, band, stroke, expected) in [
            (&cats[..], 1.0, 4.0, 4.0),
            (&cats[..], 1.0, 10.0, 10.0),
            (&cats[..], 1.0, 1.0, 2.0),
            (&cats[..], 0.7, 4.0, 2.0),
            (&six[..], 0.95, 6.0, 3.0),
            (&six[..], 0.9, 10.0, 4.0),
        ] {
            let mut j = make_vertical_payload(cats, false);
            j["resolved"]["style"]["mark"]["band_width"] = json!(band);
            j["resolved"]["style"]["mark"]["border"] =
                json!({"width": stroke, "color": "#d62728", "radius": 0.0});
            let svg = render(&make_payload(j), &test_fonts()).expect("renders");
            assert_eq!(
                plot_right_geometry(&svg).0,
                expected,
                "{} bands, band {band}, stroke {stroke}: {svg}",
                cats.len()
            );
        }
    }

    /// The same reserve through `BandColumns` for a side-by-side grouped and
    /// a stacked column, with the margin read against the drawn bars: it is
    /// the zero rule's 2px floor or `ceil(stroke - (plot_w - rightmost bar
    /// edge))`. The `GAP` case drops the last category's last series, so the
    /// rightmost drawn bar is the earlier series' column and the empty slot
    /// past it reserves nothing (`drawn_ends` follows the data rows, as Vega
    /// bounds one rect per row).
    #[test]
    fn grouped_and_stacked_fit_reserves_follow_the_drawn_end_bars() {
        use serde_json::json;
        let cats: Vec<(String, f64)> =
            (0..30).map(|i| (format!("C{i:02}"), 900.0 + 40.0 * i as f64)).collect();
        let cats: Vec<(&str, f64)> = cats.iter().map(|(c, v)| (c.as_str(), *v)).collect();
        // (shape, drop the last category's last series, expected reserve)
        for (shape, gap, expected) in [
            ("grouped", false, 4.0),
            ("grouped", true, 2.0),
            ("stacked", false, 8.0),
        ] {
            let mut j = make_vertical_payload(&cats, true);
            j["resolved"]["style"]["mark"]["band_width"] = json!(1.0);
            j["resolved"]["style"]["mark"]["border"] =
                json!({"width": 8.0, "color": "#d62728", "radius": 0.0});
            j["resolved"]["legend"]["visible"] = json!(false);
            if shape == "stacked" {
                j["resolved"]["stack"] = json!("zero");
                j["resolved"]["stacked_domain_max"] = json!(3500.0);
            }
            if gap {
                j["rows"].as_array_mut().unwrap().pop();
            }
            let svg = render(&make_payload(j), &test_fonts()).expect("renders");
            let (margin, plot_w, bars_right) = plot_right_geometry(&svg);
            let oracle = (8.0 - (plot_w - bars_right)).ceil().max(2.0);
            assert_eq!(margin, oracle, "{shape} gap {gap}: the drawn bars' overhang");
            assert_eq!(margin, expected, "{shape} gap {gap}: {svg}");
        }
    }

    /// A rendered vertical bar's plot group `translate` y.
    fn plot_top(svg: &str) -> f64 {
        let open = svg.find("<g transform=\"translate(").expect("plot group");
        let translate = svg[open..].split(')').next().unwrap();
        translate.split(',').nth(1).unwrap().trim().parse().expect("numeric translate")
    }

    /// `plot_top` and the plot height, read off the zero rule's `y1` (it spans
    /// `[0, plot_w]` along the baseline).
    fn plot_top_and_height(svg: &str) -> (f64, f64) {
        let rule_end = svg.find("stroke=\"#8A8C8F\"").expect("zero rule");
        let rule = &svg[svg[..rule_end].rfind("<line").unwrap()..];
        let at = rule.find(" y1=\"").expect("attribute") + " y1=\"".len();
        (plot_top(svg), rule[at..].split('"').next().unwrap().parse().expect("numeric y1"))
    }

    /// A bordered bar's rect bound is stroke-expanded in y too
    /// (`boundStroke`, the full width — `vega.js:15959-15965`): a bar reaching
    /// the domain top overhangs the plot above by its stroke, which the fit
    /// reserves, shrinking the plot by it. A domain with headroom wider than
    /// the stroke reserves nothing. The ladder stops at the baseline so a top
    /// tick label's own ink does not mask the stroke.
    /// `test_a_bar_rect_bound_reserves_its_full_stroke_past_the_plot_edge_in_y`
    /// pins the same law against Vega.
    #[test]
    fn a_bar_reaching_the_domain_top_reserves_its_stroke_above_the_plot() {
        use serde_json::json;
        let cats = [("A", 1800.0), ("B", 1200.0), ("C", 900.0)];
        let plot = |domain_max: f64, stroke: Option<f64>| {
            let mut j = make_vertical_payload(&cats, false);
            j["resolved"]["style"]["axis_y"]["domain_max"] = json!(domain_max);
            j["resolved"]["style"]["axis_y"]["tick_values"] = json!([0.0]);
            if let Some(width) = stroke {
                j["resolved"]["style"]["mark"]["border"] =
                    json!({"width": width, "color": "#d62728", "radius": 0.0});
            }
            let svg = render(&make_payload(j), &test_fonts()).expect("renders");
            plot_top_and_height(&svg)
        };
        let (top, height) = plot(1800.0, None);
        let (top_on_edge, height_on_edge) = plot(1800.0, Some(8.0));
        assert_eq!(
            top_on_edge - top,
            8.0,
            "the tallest bar on the domain top reserves its stroke"
        );
        assert_eq!(height - height_on_edge, 8.0, "and the plot gives up the same height");
        let (top_headroom, height_headroom) = plot(3600.0, Some(8.0));
        assert_eq!(top_headroom, plot(3600.0, None).0, "headroom absorbs the stroke");
        assert_eq!(height_headroom, plot(3600.0, None).1);
        // The baseline reserve below the plot grows 1:1 with the stroke once it
        // outreaches the category labels' own ink.
        let (_, height_30) = plot(3600.0, Some(30.0));
        let (_, height_40) = plot(3600.0, Some(40.0));
        assert_eq!(height_30 - height_40, 10.0, "bars on the baseline reserve their stroke below");
    }

    /// The same y term for stacked columns (the sign-split running sums) and a
    /// normalized stack, whose extent ends at 1: the stack top on the domain
    /// top reserves the stroke above the plot.
    #[test]
    fn a_stack_reaching_the_domain_top_reserves_its_stroke_above_the_plot() {
        use serde_json::json;
        let cats = [("A", 1000.0), ("B", 600.0)];
        // (stack, stacked_domain_max); the tallest stack is A's 1000 + 600.
        for (stack, domain_max, reserves) in [
            ("zero", 1600.0, true),
            ("zero", 3200.0, false),
            ("normalize", 1.0, true),
        ] {
            let top = |stroke: f64| {
                let mut j = make_vertical_payload(&cats, true);
                j["resolved"]["stack"] = json!(stack);
                j["resolved"]["stacked_domain_max"] = json!(domain_max);
                j["resolved"]["legend"]["visible"] = json!(false);
                j["resolved"]["style"]["axis_y"]["tick_values"] = json!([0.0]);
                j["resolved"]["style"]["mark"]["border"] =
                    json!({"width": stroke, "color": "#d62728", "radius": 0.0});
                let svg = render(&make_payload(j), &test_fonts()).expect("renders");
                plot_top_and_height(&svg).0
            };
            // Both strokes outreach the normalized axis's own "100%" tick label.
            let grown = top(30.0) - top(20.0);
            assert_eq!(grown, if reserves { 10.0 } else { 0.0 }, "{stack} {domain_max}");
        }
    }

    /// A ranged bar spans its two ends with no zero seed: its stroke is
    /// reserved above the plot when its highest end sits on the domain top,
    /// and not where the domain leaves headroom. (The ranged rule's floor
    /// edge has no zero rule to read the plot height off; the probe table
    /// pins it.)
    #[test]
    fn a_ranged_bar_ending_on_the_domain_top_reserves_its_stroke_above_the_plot() {
        use serde_json::json;
        for (domain_max, grows) in [(2000.0, true), (3000.0, false)] {
            let top = |stroke: f64| {
                let mut j = make_vertical_payload(&[("Alpha", 1000.0), ("Beta", 2000.0)], false);
                for row in j["rows"].as_array_mut().unwrap() {
                    row["start"] = json!(100.0);
                }
                j["resolved"]["y_start"] = json!("start");
                let axis_y = &mut j["resolved"]["style"]["axis_y"];
                axis_y["domain_min"] = json!(100.0);
                axis_y["domain_max"] = json!(domain_max);
                axis_y["tick_values"] = json!([100.0]);
                j["resolved"]["style"]["mark"]["border"] =
                    json!({"width": stroke, "color": "#d62728", "radius": 0.0});
                let svg = render(&make_payload(j), &test_fonts()).expect("renders");
                plot_top(&svg)
            };
            assert_eq!(top(30.0) - top(20.0), if grows { 10.0 } else { 0.0 }, "{domain_max}");
        }
    }

    #[test]
    fn vertical_grouped_bar_renders() {
        let payload_json = make_vertical_payload(
            &[("Alpha", 1800.0), ("Beta", 1200.0)],
            true,
        );
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("grouped vertical renders");
        assert!(svg.contains("<svg"), "SVG produced");
    }

    /// Payload for the HORIZONTAL arm: a band y, a value x, no legend.
    fn make_horizontal_payload() -> serde_json::Value {
        use serde_json::json;

        let resolved = json!({
            "link_open": "page",
            "id": "hbar_test",
            "title": null,
            "title_style": null,
            "background": "#ffffff",
            "canvas": "#ffffff",
            "x": "product",
            "y": "revenue",
            "color": null,
            "orientation": "horizontal",
            "stack": "none",
            "palette": ["#3164a3"],
            "multiples": null,
            "field_labels": {}, "wide_measures": [],
            "layers": [],
            "legend": {"visible": false, "position": {"edge": "top", "align": "start", "overlay": false}, "direction": "horizontal", "title": {"font": {"case": "title", "family": TEST_FONT}}, "label": {"padding": 8.0, "font": {"family": TEST_FONT, "size": 11.0}}},
            "style": {
                "axis_x": {
                    "grid": {"visible": false, "threshold": null, "width": 1.0, "color": "#D0D3D9", "opacity": 1.0},
                    "ticks": {"color": "#EDEFF2", "visible": true, "count": 6.0},
                    "labels": {
                        "font": {"family": TEST_FONT, "color": "#626366", "size": 11.0, "weight": "400"},
                        "padding": 6.0, "min_gap": 4.0, "format": null, "visible": true, "align": "left"
                    },
                    "title": {"font": {"family": TEST_FONT, "weight": "600"}, "padding": 0.0, "visible": false},
                    "position": null, "mirror": null, "band_position": 0.5, "scale": {},
                    "tick_values": [], "domain_max": null, "domain_min": null, "ruler": null,
                    "time_unit": null, "fiscal_year_start_month": 1, "is_quantitative": true, "zero_anchored": false, "fill": null, "line": null
                },
                "axis_y": {
                    "grid": {"visible": true, "threshold": {"color": "#8A8C8F", "width": 2.0}, "width": 1.0, "color": "#D0D3D9", "opacity": 1.0},
                    "ticks": {"color": "#D0D3D9", "visible": true, "count": 6.0},
                    "labels": {
                        "font": {"family": TEST_FONT, "color": "#626366", "size": 11.0, "weight": "400"},
                        "padding": 6.0, "min_gap": null, "format": null, "visible": true
                    },
                    "title": {"font": {"family": TEST_FONT, "weight": "600"}, "padding": 0.0, "visible": false},
                    "position": "left", "mirror": null, "band_position": 0.5, "scale": {},
                    "tick_values": [0.0, 500.0, 1000.0], "domain_max": 1000.0, "domain_min": null,
                    "ruler": null, "tick_label": {"format": ",.0~f"},
                    "time_unit": null, "fiscal_year_start_month": 1, "is_quantitative": true, "zero_anchored": false, "fill": null, "line": null
                },
                "mark": {"band_width": 0.7, "size": 20.0, "border": {"width": null, "color": null, "radius": 0.0}, "labels": {"visible": false}},
                "grouped": {"padding_inner": 0.2, "padding_outer": 0.2},
                "single_series_fill": "#3164a3",
                "series_label": {"font_family": "'Inter'", "font_size": 11.0, "font_weight": "400", "font_style": "normal", "dark_companion_palette": [], "pin_companions": []},
                "endpoint_labels": {"visible": false, "height": 20.0, "label_offset": 6.0}
            }
        });

        let payload_json = json!({
            "resolved": resolved,
            "rows": [
                {"product": "Alpha", "revenue": 800.0},
                {"product": "Beta", "revenue": 600.0}
            ],
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": {"left": 16.0, "right": 16.0, "top": 16.0, "bottom": 16.0}
        });

        payload_json
    }

    #[test]
    fn horizontal_bar_orientation_unchanged() {
        let payload = make_payload(make_horizontal_payload());
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("horizontal bar renders");
        assert!(svg.contains("<svg"));
    }

    /// `render_horizontal`'s own `bg` (see `BarResolved::background`) feeds
    /// the canvas rect, and its `r.canvas` (see `BarResolved::canvas`) the
    /// inside-bar `top`-position value label's default fill via
    /// `mark_labels` — the same law the vertical arm's own test pins,
    /// ported to the horizontal panel function.
    #[test]
    fn horizontal_canvas_rect_reads_the_background_and_inside_bar_label_the_canvas() {
        let mut j = make_horizontal_payload();
        j["resolved"]["style"]["mark"]["labels"] = serde_json::json!({
            "visible": true, "position": "top",
            "field": null, "format": null, "dx": null, "dy": null, "font": null
        });
        j["resolved"]["background"] = serde_json::json!("transparent");
        j["resolved"]["canvas"] = serde_json::json!("#d9a45b");
        j["background"] = serde_json::json!("#ffffff");
        let svg = render(&make_payload(j), &test_fonts()).expect("renders");
        assert!(svg.contains("<rect width=\"600\" height=\"400\" fill=\"transparent\"/>"), "{svg}");
        let i = svg.find(">800<").expect("label present");
        let tag = &svg[svg[..i].rfind("<text").unwrap()..i];
        assert!(tag.contains("fill=\"#d9a45b\""), "top label paints the chart's own canvas: {tag}");
    }

    /// A horizontal bar's tooltip headers a detected calendar-grain x by that grain,
    /// as the vertical arm does (`header_tooltip_field` is orientation-independent).
    #[test]
    fn horizontal_bar_over_a_temporal_x_headers_its_tooltip_by_the_detected_grain() {
        use serde_json::json;
        let _locale = crate::time_locale::en_us_scope();
        let mut payload_json = make_horizontal_payload();
        payload_json["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        payload_json["rows"] = json!([
            {"product": "2024-01-01", "revenue": 800.0},
            {"product": "2024-02-01", "revenue": 600.0}
        ]);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        assert!(svg.contains("Jan 2024") && !svg.contains("aria-label=\"\u{2061}2024-01-01"), "{svg}");
    }

    /// Same law as `vertical_bar_standard_date_with_no_detected_bucket_
    /// formats_a_full_header`, on the horizontal arm: a standard `YYYY-MM-DD`
    /// x that `detect_time_unit` doesn't bucket (28-day steps exceed the
    /// 14-day median-gap cutoff) doesn't hit the refusal above (that gate
    /// only fires on a DETECTED calendar grain) and must still header
    /// through the continuous-date fallback, never the raw ISO string.
    /// Regression coverage for the bug where `render_horizontal` hardcoded
    /// `is_standard_date` false regardless of the actual x shape.
    #[test]
    fn horizontal_bar_standard_date_with_no_detected_bucket_formats_a_full_header() {
        use serde_json::json;
        let mut payload_json = make_horizontal_payload();
        payload_json["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        payload_json["rows"] = json!([
            {"product": "2025-01-01", "revenue": 800.0},
            {"product": "2025-01-29", "revenue": 600.0},
            {"product": "2025-02-26", "revenue": 400.0}
        ]);
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(
            svg.contains("aria-label=\"\u{2061}1 Jan 2025; Revenue: 800\""),
            "a standard-date x with no detected bucket headers the tooltip through \
             the continuous fallback, not the raw ISO string: {svg}"
        );
    }

    // ── Wide measures ────────────────────────────────────────────────────

    /// A two-measure (`y: [revenue, profit]`) wide horizontal bar — same
    /// shape as `layered_product_metrics`: no stack, no `color:` dimension,
    /// a top legend. The resolver bakes `y` as `WIDE_VALUE_FIELD`; the raw
    /// `revenue`/`profit` columns still live on each row.
    fn make_wide_horizontal_payload() -> serde_json::Value {
        use serde_json::json;
        let mut p = make_horizontal_payload();
        p["resolved"]["y"] = json!("__dbt_charts_wide_value__");
        p["resolved"]["wide_measures"] = json!(["revenue", "profit"]);
        p["resolved"]["legend"] = json!({
            "visible": true, "position": {"edge": "top", "align": "start", "overlay": false}, "direction": "horizontal",
            "title": {"font": {"case": "title", "family": TEST_FONT}},
            "label": {"padding": 8.0, "font": {"family": TEST_FONT, "size": 11.0}}
        });
        p["rows"] = json!([
            {"product": "Alpha", "revenue": 800.0, "profit": 200.0},
            {"product": "Beta", "revenue": 600.0, "profit": 150.0}
        ]);
        p
    }

    /// Two sub-bands per category (grouped, not stacked) and a two-entry
    /// legend in SORTED series-name order — `wide_series_names(["revenue",
    /// "profit"], None, _)` sorts to `["profit", "revenue"]`, matching
    /// `layered_product_metrics.ref.svg`'s own legend text order.
    #[test]
    fn wide_horizontal_bar_draws_two_sub_bands_with_a_sorted_legend() {
        let payload = make_payload(make_wide_horizontal_payload());
        let svg = render(&payload, &test_fonts()).expect("wide horizontal bar renders");
        let bars: Vec<_> = parse_rects(&svg).into_iter().filter(|r| r.2 != 10.0).collect();
        assert_eq!(bars.len(), 4, "two sub-bands x two categories: {svg}");
        let profit_pos = svg.find(">Profit<").expect("profit legend entry present");
        let revenue_pos = svg.find(">Revenue<").expect("revenue legend entry present");
        assert!(profit_pos < revenue_pos, "sorted alphabetically, profit before Revenue: {svg}");
    }

    /// `legend.values` may name a wide measure by its raw name or its painted label.
    #[test]
    fn a_wide_legend_value_may_name_a_measure_by_its_label() {
        use serde_json::json;
        let mut p = make_wide_horizontal_payload();
        p["resolved"]["wide_measures"] = json!(["revenue", "units_sold"]);
        p["resolved"]["field_labels"] = json!({"units_sold": "Volume"});
        p["rows"] = json!([
            {"product": "Alpha", "revenue": 800.0, "units_sold": 200.0},
            {"product": "Beta", "revenue": 600.0, "units_sold": 150.0}
        ]);
        let order = |values: serde_json::Value| {
            let mut p = p.clone();
            p["resolved"]["legend"]["values"] = values;
            let svg = render(&make_payload(p), &test_fonts()).expect("wide bar renders");
            (svg.find(">Volume<").expect("label paints"), svg.find(">Revenue<").expect("revenue paints"))
        };
        let (volume, revenue) = order(json!(["revenue", "Volume"]));
        assert!(revenue < volume);
        let (volume, revenue) = order(json!(["Volume", "revenue"]));
        assert!(volume < revenue);
        let (volume, revenue) = order(json!(["units_sold", "revenue"]));
        assert!(volume < revenue);
    }

    /// The wide legend paints each measure's label (`wide_measure_labels_for`),
    /// an authored `field_labels` entry verbatim; the raw column name never.
    #[test]
    fn wide_bar_legend_paints_measure_labels_on_both_orientations() {
        use serde_json::json;
        for orientation in ["horizontal", "vertical"] {
            let mut p = make_wide_horizontal_payload();
            p["resolved"]["orientation"] = json!(orientation);
            p["resolved"]["wide_measures"] = json!(["revenue", "units_sold"]);
            p["rows"] = json!([
                {"product": "Alpha", "revenue": 800.0, "units_sold": 200.0},
                {"product": "Beta", "revenue": 600.0, "units_sold": 150.0}
            ]);
            let svg = render(&make_payload(p.clone()), &test_fonts()).expect("wide bar renders");
            assert!(svg.contains(">Units sold<"), "{orientation}: humanized: {svg}");
            assert!(!svg.contains(">units_sold<"), "{orientation}: raw name never paints: {svg}");

            p["resolved"]["field_labels"] = json!({"units_sold": "Units SHIPPED"});
            let svg = render(&make_payload(p), &test_fonts()).expect("wide bar renders");
            assert!(svg.contains(">Units SHIPPED<"), "{orientation}: field label verbatim: {svg}");
        }
    }

    /// A wide-measure chart draws no value labels in production, so an
    /// authored `labels` renders label-less instead of refusing.
    #[test]
    fn wide_bar_labels_are_production_s_no_op() {
        let labels = serde_json::json!({
            "visible": true, "field": null, "format": ".3~s", "dx": null, "dy": null,
            "font": null, "position": "top"
        });
        // The dimension-crossed shape (`color: region`) is the one a
        // `color.is_some()` gate alone would refuse.
        let crossed = serde_json::json!([
            {"product": "Alpha", "region": "N", "revenue": 800.0, "profit": 200.0},
            {"product": "Alpha", "region": "S", "revenue": 500.0, "profit": 100.0},
            {"product": "Beta", "region": "N", "revenue": 600.0, "profit": 150.0},
            {"product": "Beta", "region": "S", "revenue": 300.0, "profit": -90.0}
        ]);
        let base = make_wide_horizontal_payload();
        for orientation in ["horizontal", "vertical"] {
            for (color, rows) in [(None, base["rows"].clone()), (Some("region"), crossed.clone())] {
                let mut p = base.clone();
                p["resolved"]["orientation"] = serde_json::json!(orientation);
                p["resolved"]["color"] = serde_json::json!(color);
                p["rows"] = rows;
                let unlabeled = render(&make_payload(p.clone()), &test_fonts()).expect("wide bar renders");
                p["resolved"]["style"]["mark"]["labels"] = labels.clone();
                let labeled = render(&make_payload(p), &test_fonts()).expect("wide bar renders");
                assert_eq!(labeled, unlabeled, "{orientation} {color:?}: a wide chart paints no value labels");
            }
        }
    }

    /// `wide_series_names` — the sorted, authored-measure-COMPLETE
    /// color domain — must feed `n_series`/the legend/palette index, not a
    /// scan of the folded rows (`color_domain_of`'s own input). `mid` is
    /// null on every row (absent entirely, same fold outcome as an explicit
    /// null cell), so a row scan sees only `["alpha", "zeta"]` and shifts
    /// `zeta` into slot 1; the sorted, complete domain is `["alpha", "mid",
    /// "zeta"]` and `zeta` must keep slot 2, with `mid` still a legend entry.
    #[test]
    fn wide_bar_null_measure_still_owns_its_sorted_legend_slot() {
        use serde_json::json;
        let mut p = make_wide_horizontal_payload();
        p["resolved"]["wide_measures"] = json!(["alpha", "mid", "zeta"]);
        p["resolved"]["palette"] = json!(["#111111", "#222222", "#333333"]);
        p["rows"] = json!([
            {"product": "Alpha", "alpha": 800.0, "zeta": 200.0},
            {"product": "Beta", "alpha": 600.0, "zeta": 150.0}
        ]);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let mut entries = Vec::new();
        for (pos, _) in svg.match_indices(&format!("width=\"{LEGEND_SWATCH}\"")) {
            let snippet = &svg[pos..(pos + 500).min(svg.len())];
            let fill = snippet.split("fill=\"").nth(1).unwrap().split('"').next().unwrap();
            let label = legend_label_text(snippet);
            entries.push((label.to_string(), fill.to_string()));
        }
        assert_eq!(
            entries,
            vec![
                ("Alpha".to_string(), "#111111".to_string()),
                ("Mid".to_string(), "#222222".to_string()),
                ("Zeta".to_string(), "#333333".to_string()),
            ],
            "mid's total absence must not shift zeta from slot 2 to slot 1: {svg}"
        );
    }

    /// A wide (`revenue`, `profit`) horizontal stacked bar: the stack
    /// accumulates in `sorted_series_by_stack_order`'s baseline order over
    /// the unfolded rows (`_emit_wide_bar`), and the palette follows it.
    fn make_wide_stacked_payload() -> serde_json::Value {
        use serde_json::json;
        let mut p = make_wide_horizontal_payload();
        p["resolved"]["stack"] = json!("zero");
        p["resolved"]["stacked_domain_max"] = json!(1000.0);
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["palette"] = json!(["#111111", "#222222"]);
        // profit is listed first but revenue carries the larger global sum.
        p["resolved"]["wide_measures"] = json!(["profit", "revenue"]);
        p
    }

    /// `x` of the first `<rect>` painted in `fill`.
    fn rect_x_of(svg: &str, fill: &str) -> f64 {
        let marker = format!("fill=\"{fill}\"");
        let at = svg.find(&marker).unwrap_or_else(|| panic!("no {fill} rect: {svg}"));
        let tag_start = svg[..at].rfind("<rect").unwrap_or_else(|| panic!("no <rect before {fill}: {svg}"));
        let tag = &svg[tag_start..at + marker.len()];
        tag.split(" x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
    }

    #[test]
    fn wide_horizontal_stacked_bar_puts_the_largest_measure_on_the_baseline() {
        let svg = render(&make_payload(make_wide_stacked_payload()), &test_fonts()).expect("renders");
        assert!(
            rect_x_of(&svg, "#111111") < rect_x_of(&svg, "#222222"),
            "revenue (palette[0], largest sum) sits on the baseline, profit stacks after it: {svg}"
        );
    }

    /// A wide bar with no `x` field can't even deserialize — `BarResolved.x`
    /// is a required `String`, so an authorless `x` (production's own
    /// `ChartDataError` for this case) already refuses here, wide or not.
    #[test]
    fn wide_bar_without_x_refuses() {
        let mut p = make_wide_horizontal_payload();
        p["resolved"]["x"] = Value::Null;
        let payload: Payload = serde_json::from_value(p).expect("payload JSON itself parses");
        let err = render(&payload, &test_fonts()).unwrap_err();
        assert!(err.contains("bar payload"), "{err}");
    }

    /// A wide bar's strip reads the rows as queried and, like production's
    /// fold that keeps every column, paints each cell once per measure.
    #[test]
    fn wide_bar_with_support_table_paints_each_cell_once_per_measure() {
        use serde_json::json;
        let mut p = make_wide_horizontal_payload();
        p["resolved"]["support_table"] = json!({
            "entries": [{"aggregate": "sum", "source": "revenue", "format": ",d", "label": "Total"}]
        });
        p["resolved"]["effective_support_table_style"] = support_table_style_json();
        p["resolved"]["effective_support_table_style"]["position"] = json!("right");
        let svg = render(&make_payload(p), &test_fonts()).expect("wide bar with a strip renders");
        assert_eq!(svg.matches(">800<").count(), 2, "{svg}");
        assert_eq!(svg.matches(">600<").count(), 2, "{svg}");
    }

    /// B6 — Vega's `labelLimit` truncation: a category label wider than
    /// `labels.max_width` (or the 180px `DEFAULT_VL_LABEL_LIMIT`) is drawn
    /// truncated, not full — `bar_review`'s reference y label reads
    /// "Emerging Product and Innovatio…", never the full 37-char string.
    /// The drawn string and the reserved gutter must come from the SAME
    /// truncated measure — this pins the wiring by checking the drawn
    /// text's own measured width never exceeds the authored limit.
    #[test]
    fn horizontal_bar_category_label_wraps_then_truncates_past_the_label_limit() {
        use serde_json::json;
        let mut payload_json = make_horizontal_payload();
        payload_json["rows"] = json!([
            {"product": "Emerging Product and Innovation Bets", "revenue": 800.0},
            {"product": "Beta", "revenue": 600.0}
        ]);
        payload_json["resolved"]["style"]["axis_x"]["labels"]["max_width"] = json!(60.0);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        let tag_start = svg
            .find("<title>Emerging Product and Innovation Bets</title>")
            .and_then(|p| svg[..p].rfind("<text"))
            .expect("a cut label keeps its full name on hover");
        let tag_end = tag_start + svg[tag_start..].find("</text>").expect("closing tag");
        let label = &svg[tag_start..tag_end];
        assert!(
            label.contains("<title>Emerging Product and Innovation Bets</title>"),
            "a cut label keeps its full name on hover: {label}"
        );
        let lines: Vec<&str> = label
            .split("<tspan")
            .skip(1)
            .map(|t| t.split_once('>').expect("tag close").1.split("</tspan>").next().expect("closing tspan"))
            .collect();
        assert_eq!(lines.len(), 2, "wraps onto two lines: {label}");
        assert!(!lines[0].contains('\u{2026}') && lines[1].ends_with('\u{2026}'), "only line two is cut: {lines:?}");
        // Vega's own invariant: the CUTOFF decision measures the drawn
        // prefix with trailing whitespace ignored (`truncate_label`'s
        // `measure_cand`), not the untrimmed emitted string, so a cut that
        // lands right after a word boundary can sit a hair over `limit`.
        for drawn in lines {
            let trimmed_prefix = drawn.trim_end_matches('\u{2026}').trim_end();
            let w = fonts
                .measure(&format!("{trimmed_prefix}\u{2026}"), TEST_FONT, 11.0)
                .expect("measure");
            assert!(
                w <= 60.0 + 1e-6,
                "drawn line {drawn:?} trimmed-prefix width {w} exceeds the authored 60px \
                 limit — the drawn string and the reserved gutter must agree"
            );
        }
    }

    /// B8 — Vega's `labelFlush`: `flush(range, scale(datum.value), 1, "left",
    /// "right", "center")` is a **1px proximity test in range-space
    /// pixels**, never exact equality on the tick's domain value. This
    /// fixture reproduces `bar_review`'s shape — an exact headroom-baked
    /// `domain_max` a hair above the last VISIBLE tick of 200000 (Vega's
    /// scenegraph puts `bar_review`'s at x 329.55 of a 331px range, a 1.45px
    /// gap) — scaled so this fixture's own 513px plot lands the gap inside
    /// (1.0, 1.5]: the only window where the crispness-delta bug (a +0.5px
    /// error on one side of the comparison) flips the anchor, so the test
    /// dies on that mutant and not just on the exact-equality one. The
    /// reference keeps such a tick `text-anchor="middle"`, reserving half
    /// its measured width as edge chrome. (An earlier version of this test used `domain_max = 1000.001`
    /// against a last tick of 1000 — a ~0.0002px gap — and asserted
    /// `middle`; Vega's own `flush` renders that gap `right`, since
    /// `l < r && l <= 1.0`. That fixture pinned a departure from Vega, not
    /// Vega itself.)
    fn assert_last_val_label_anchor(domain_max: f64, tick_values: &[f64], expect_flush: bool) {
        use serde_json::json;
        let mut payload_json = make_horizontal_payload();
        payload_json["resolved"]["style"]["axis_y"]["tick_values"] = json!(tick_values);
        payload_json["resolved"]["style"]["axis_y"]["domain_max"] = json!(domain_max);
        payload_json["rows"] = json!([
            {"product": "Alpha", "revenue": 186000.0},
            {"product": "Beta", "revenue": 140000.0}
        ]);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        let tag_start = svg.find(">200,000</text>").and_then(|pos| svg[..pos].rfind("<text"))
            .expect("last value-axis label <text> tag found");
        let tag = &svg[tag_start..(tag_start + 200).min(svg.len())];
        let anchor = if expect_flush { "end" } else { "middle" };
        assert!(
            tag.contains(&format!("text-anchor=\"{anchor}\"")),
            "expected text-anchor={anchor:?} at domain_max={domain_max}: {tag}"
        );

        // The first tick (0) still sits exactly on the domain min in range
        // space (gap 0), so it stays flush at the plot's left edge either way.
        let zero_start = svg.find(">0</text>").and_then(|pos| svg[..pos].rfind("<text"))
            .expect("zero value-axis label <text> tag found");
        let zero_tag = &svg[zero_start..(zero_start + 200).min(svg.len())];
        assert!(
            zero_tag.contains("text-anchor=\"start\""),
            "domain-min tick stays flush: {zero_tag}"
        );

        if !expect_flush {
            // Pin the fixture inside the crispness-delta bug's discriminating
            // window, delta-free (both labels share the axis group's
            // translate): the plot width is a fixed point of the fit and
            // could drift the gap back out silently.
            fn text_x(tag: &str) -> f64 {
                let i = tag.find(" x=\"").expect("x attr") + 4;
                let j = tag[i..].find('"').expect("x attr end") + i;
                tag[i..j].parse().expect("x is numeric")
            }
            let span = text_x(tag) - text_x(zero_tag);
            let gap = span * (domain_max / tick_values.last().unwrap() - 1.0);
            assert!(gap > 1.0 && gap <= 1.5, "fixture gap {gap}px is outside (1.0, 1.5]");
        }
    }

    #[test]
    fn horizontal_bar_measure_label_short_of_headroom_stays_centered() {
        assert_last_val_label_anchor(
            200500.0,
            &[0.0, 50000.0, 100000.0, 150000.0, 200000.0],
            false,
        );
    }

    /// The complement Vega's own probe pins: a gap under 1px still flushes,
    /// even though the tick's domain value does not equal `domain_max` exactly.
    #[test]
    fn horizontal_bar_measure_label_within_a_pixel_of_the_range_end_still_flushes() {
        assert_last_val_label_anchor(
            200001.0,
            &[0.0, 50000.0, 100000.0, 150000.0, 200000.0],
            true,
        );
    }

    /// `labels.flush` is Vega-Lite's `labelFlush`: `false` centers every
    /// label (no edge flush at all), a number is the threshold in range
    /// pixels, `true`/unset is the 1px default. Anything else refuses.
    fn render_with_flush(flush: serde_json::Value) -> Result<String, String> {
        use serde_json::json;
        let mut payload_json = make_horizontal_payload();
        payload_json["resolved"]["style"]["axis_y"]["tick_values"] =
            json!([0.0, 50000.0, 100000.0, 150000.0, 200000.0]);
        payload_json["resolved"]["style"]["axis_y"]["domain_max"] = json!(200500.0);
        payload_json["resolved"]["style"]["axis_y"]["labels"]["flush"] = flush;
        payload_json["rows"] = json!([
            {"product": "Alpha", "revenue": 186000.0},
            {"product": "Beta", "revenue": 140000.0}
        ]);
        render(&make_payload(payload_json), &test_fonts()).map_err(|e| e.to_string())
    }

    fn anchor_of(svg: &str, text: &str) -> String {
        let start = svg.find(text).and_then(|pos| svg[..pos].rfind("<text")).expect("label tag");
        let tag = &svg[start..(start + 200).min(svg.len())];
        let i = tag.find("text-anchor=\"").expect("anchor") + 13;
        tag[i..i + tag[i..].find('"').unwrap()].to_string()
    }

    #[test]
    fn authored_flush_false_centers_every_measure_label() {
        let svg = render_with_flush(serde_json::json!(false)).expect("renders");
        assert_eq!(anchor_of(&svg, ">0</text>"), "middle");
        assert_eq!(anchor_of(&svg, ">200,000</text>"), "middle");
    }

    #[test]
    fn authored_flush_number_is_the_threshold_in_range_pixels() {
        // 1 is Vega's default threshold — byte-identical to `true`; 4 takes
        // the 1.28px-gap tick to the range end.
        let one = render_with_flush(serde_json::json!(1)).expect("renders");
        let default = render_with_flush(serde_json::json!(true)).expect("renders");
        assert_eq!(one, default);
        let four = render_with_flush(serde_json::json!(4)).expect("renders");
        assert_eq!(anchor_of(&four, ">200,000</text>"), "end");
    }

    #[test]
    fn authored_flush_of_another_shape_refuses() {
        let err = render_with_flush(serde_json::json!("auto")).expect_err("refuses");
        assert!(err.contains("labels.flush=\"auto\""), "{err}");
    }

    fn stark_weight_grid_svg() -> String {
        let mut payload_json = make_horizontal_payload();
        payload_json["resolved"]["style"]["axis_y"]["grid"]["width"] = serde_json::json!(1.5);
        render(&make_payload(payload_json), &test_fonts()).expect("renders")
    }

    /// Vega draws the tick-0 gridline, under the zero rule. The rule (2px,
    /// centered on x=0) covers a 1px line at x=0.5 completely but leaves
    /// a 1.5px one's outer 0.25px showing, so the line is never skipped.
    #[test]
    fn horizontal_bar_draws_the_tick_zero_gridline_under_the_zero_rule() {
        let svg = stark_weight_grid_svg();
        assert_eq!(svg.matches("stroke=\"#D0D3D9\" stroke-width=\"1.5\"").count(), 3, "{svg}");
    }

    /// The plot's right edge: the plot group's left plus the last gridline's
    /// x (Vega's crisp +0.5 removed), so the draw sits on the domain max's rung.
    fn horizontal_plot_right(svg: &str) -> f64 {
        let last_grid_x = svg
            .match_indices("<line x1=\"")
            .map(|(i, _)| &svg[i..svg[i..].find("/>").unwrap() + i])
            .filter(|tag| tag.contains("#D0D3D9"))
            .map(|tag| tag_attr(tag, "x1"))
            .fold(f64::MIN, f64::max);
        plot_group_left(svg) + last_grid_x - 0.5
    }

    /// A bar's rect is stroke-expanded (`boundStroke`, full width), so one
    /// ending on the domain max overhangs the plot's right edge by its 1px
    /// stroke and the fit reserves it: the plot ends 1px inside the padded box,
    /// as production lays it out (stark bar board: plot right at 583 of a
    /// 584 box). A stroke-less bar reserves nothing.
    #[test]
    fn horizontal_bar_ending_on_the_domain_max_reserves_its_stroke() {
        let plot_right = |stroke: serde_json::Value| {
            let mut payload_json = make_horizontal_payload();
            payload_json["resolved"]["style"]["mark"]["border"] =
                serde_json::json!({"width": 1.0, "color": stroke, "radius": 0.0});
            payload_json["rows"][0]["revenue"] = serde_json::json!(1000.0);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            horizontal_plot_right(&svg)
        };
        assert_eq!(plot_right(serde_json::json!("#ffffff")), 600.0 - 16.0 - 1.0);
        assert_eq!(plot_right(serde_json::json!(null)), 600.0 - 16.0);
    }

    /// The plot right of a 600px-wide horizontal stacked-shape chart on a
    /// drawn value grid, whose bars carry a 1px stroke or none.
    fn stacked_shape_plot_right(
        stack: &str,
        rows: serde_json::Value,
        ladder: serde_json::Value,
        domain_max: f64,
        stroke: serde_json::Value,
    ) -> f64 {
        let mut payload_json = make_stacked_payload("horizontal", stack);
        payload_json["width"] = serde_json::json!(600.0);
        payload_json["rows"] = rows;
        payload_json["resolved"]["stacked_domain_max"] = serde_json::json!(domain_max);
        let axis_y = &mut payload_json["resolved"]["style"]["axis_y"];
        axis_y["grid"]["visible"] = serde_json::json!(true);
        axis_y["tick_values"] = ladder;
        axis_y["domain_max"] = serde_json::json!(domain_max);
        payload_json["resolved"]["style"]["mark"]["border"] =
            serde_json::json!({"width": 1.0, "color": stroke, "radius": 0.0});
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        horizontal_plot_right(&svg)
    }

    /// A stack's bar ends are its sign-split running sums (stackZero): the
    /// positive segments' sum, not the signed total, is the right end. Here A
    /// ends on the domain max (30 + 10, headroom 0) past its negative
    /// segment, so its stroke is reserved; a signed sum (35) would end short
    /// of it and reserve nothing.
    #[test]
    fn mixed_sign_stack_ending_on_the_domain_max_reserves_its_stroke() {
        use serde_json::json;
        let rows = json!([
            {"cat": "A", "series": "x", "val": -5.0},
            {"cat": "A", "series": "y", "val": 30.0},
            {"cat": "A", "series": "z", "val": 10.0},
            {"cat": "B", "series": "x", "val": 5.0},
            {"cat": "B", "series": "y", "val": 15.0},
        ]);
        let ladder = json!([-10.0, 0.0, 20.0, 40.0]);
        let plot_right =
            |stroke| stacked_shape_plot_right("zero", rows.clone(), ladder.clone(), 40.0, stroke);
        assert_eq!(plot_right(json!("#ffffff")), 600.0 - 16.0 - 1.0);
        assert_eq!(plot_right(json!(null)), 600.0 - 16.0);
    }

    /// A normalize stack ends every bar at 1, the domain max, whatever the
    /// data's own values.
    #[test]
    fn normalized_stack_reserves_its_stroke_at_the_domain_end() {
        use serde_json::json;
        let rows = json!([
            {"cat": "A", "series": "x", "val": 10.0},
            {"cat": "A", "series": "y", "val": 30.0},
            {"cat": "B", "series": "x", "val": 5.0},
            {"cat": "B", "series": "y", "val": 15.0},
        ]);
        let ladder = json!([0.0, 0.5, 1.0]);
        let plot_right = |stroke| {
            stacked_shape_plot_right("normalize", rows.clone(), ladder.clone(), 1.0, stroke)
        };
        assert_eq!(plot_right(json!("#ffffff")), 600.0 - 16.0 - 1.0);
        assert_eq!(plot_right(json!(null)), 600.0 - 16.0);
    }

    /// A grouped chart's bars each start at zero and end at their own value:
    /// the highest series value on the domain max reserves its stroke.
    #[test]
    fn grouped_bar_ending_on_the_domain_max_reserves_its_stroke() {
        use serde_json::json;
        let rows = json!([
            {"cat": "A", "series": "x", "val": 10.0},
            {"cat": "A", "series": "y", "val": 40.0},
            {"cat": "B", "series": "x", "val": 5.0},
            {"cat": "B", "series": "y", "val": 15.0},
        ]);
        let ladder = json!([0.0, 20.0, 40.0]);
        let plot_right =
            |stroke| stacked_shape_plot_right("none", rows.clone(), ladder.clone(), 40.0, stroke);
        assert_eq!(plot_right(json!("#ffffff")), 600.0 - 16.0 - 1.0);
        assert_eq!(plot_right(json!(null)), 600.0 - 16.0);
    }

    /// `fixtures/bar-value-label-ink-and-negative-floor/arr_chart`'s shape: a
    /// single-series horizontal bar over mixed-sign data, a computed
    /// zero-anchored ladder baked as `axis_y.tick_values`, narrative value
    /// labels, and an optional conditional color channel — `with_conditional`
    /// only exists to feed the rejection-guard tests below; bar no longer has
    /// a conditional-fill implementation to exercise.
    fn make_conditional_horizontal_payload(with_conditional: bool) -> serde_json::Value {
        use serde_json::json;

        let mut resolved = json!({
            "link_open": "page",
            "id": "hbar_conditional_test",
            "title": null,
            "title_style": null,
            "background": "#ffffff",
            "canvas": "#ffffff",
            "x": "segment",
            "y": "arr",
            "color": null,
            "orientation": "horizontal",
            "stack": "none",
            "palette": ["#1c395c"],
            "multiples": null,
            "field_labels": {}, "wide_measures": [],
            "layers": [],
            "legend": {"visible": false, "position": {"edge": "top", "align": "start", "overlay": false}, "direction": "horizontal", "title": {"font": {"case": "title", "family": TEST_FONT}}, "label": {"padding": 8.0, "font": {"family": TEST_FONT, "size": 11.0}}},
            "style": {
                "axis_x": {
                    "grid": {"visible": false, "threshold": null, "width": 1.0, "color": "#D0D3D9", "opacity": 1.0},
                    "ticks": {"color": "#EDEFF2", "visible": false, "count": 6.0},
                    "labels": {
                        "font": {"family": TEST_FONT, "color": "#626366", "size": 11.0, "weight": "400"},
                        "padding": 6.0, "min_gap": 4.0, "format": null, "visible": true, "align": "left"
                    },
                    "title": {"font": {"family": TEST_FONT, "weight": "600"}, "padding": 0.0, "visible": false},
                    "position": null, "mirror": null, "band_position": 0.5, "scale": {},
                    "tick_values": [], "domain_max": null, "domain_min": null, "ruler": null,
                    "time_unit": null, "fiscal_year_start_month": 1, "is_quantitative": true, "zero_anchored": false, "fill": null, "line": null
                },
                "axis_y": {
                    "grid": {"visible": true, "threshold": {"color": "#8A8C8F", "width": 2.0}, "width": 1.5, "color": "#D0D3D9", "opacity": 1.0},
                    "ticks": {"color": "#D0D3D9", "visible": false, "count": 6.0},
                    "labels": {
                        "font": {"family": TEST_FONT, "color": "#626366", "size": 11.0, "weight": "400"},
                        "padding": 6.0, "min_gap": 16.0, "format": ".3~s", "visible": true
                    },
                    "title": {"font": {"family": TEST_FONT, "weight": "600"}, "padding": 0.0, "visible": false},
                    "position": "left", "mirror": null, "band_position": 0.5,
                    "scale": {"headroom": 0.08, "values": null},
                    "tick_values": [-1000000.0, 0.0, 1000000.0, 2000000.0, 3000000.0],
                    "domain_max": 2160000.0, "domain_min": null,
                    "ruler": {
                        "exponent": 6, "mode": "repeat", "register": "narrative", "digit_spec": ",.1~f",
                        "anchor_at_start": false, "reservation": "", "prefix": ""
                    },
                    "tick_label": null,
                    "time_unit": null, "fiscal_year_start_month": 1, "is_quantitative": true, "zero_anchored": false, "fill": null, "line": null
                },
                "mark": {
                    "band_width": 0.8, "size": 20.0,
                    "border": {"width": null, "color": "#FAFAFA", "radius": 0.0},
                    "labels": {
                        "visible": true, "field": null, "format": ".3~s",
                        "dx": null, "dy": null,
                        "font": {"color": "#F7B068"},
                        "position": "above"
                    }
                },
                "grouped": {"padding_inner": 0.2, "padding_outer": 0.2},
                "single_series_fill": "#1c395c",
                "series_label": {"font_family": "'Inter'", "font_size": 11.0, "font_weight": "400", "font_style": "normal", "dark_companion_palette": [], "pin_companions": []},
                "endpoint_labels": {"visible": false, "height": 20.0, "label_offset": 6.0},
                "label_is_house": true
            }
        });

        if with_conditional {
            resolved["resolved_channels"] = json!({
                "color": {
                    "mode": "conditional",
                    "data_field": "arr",
                    "rules": [
                        {"gt": 0.0, "background": "#007FFF"},
                        {"lte": 0.0, "background": "#7B7B7B"}
                    ]
                }
            });
        }

        json!({
            "resolved": resolved,
            "rows": [
                {"segment": "Expansion", "arr": 2000000.0},
                {"segment": "Churn", "arr": -900000.0}
            ],
            "width": 600.0,
            "height": 400.0,
            "background": "#FAFAFA",
            "fonts_dir": null,
            "padding": {"left": 16.0, "right": 16.0, "top": 16.0, "bottom": 16.0}
        })
    }

    /// Extract the first `stroke="#8A8C8F"` line's `x1` — the zero rule.
    fn zero_rule_x(svg: &str) -> f64 {
        let start = svg.find("stroke=\"#8A8C8F\"").expect("zero rule drawn");
        let snippet = &svg[..start];
        let x1_start = snippet.rfind("x1=\"").expect("zero rule has x1");
        snippet[x1_start + 4..].split('"').next().unwrap().parse().unwrap()
    }

    #[test]
    fn horizontal_bar_negative_floor_draws_both_signs_from_the_zero_rule() {
        // Before the fix, the measure domain was hardcoded [0, domain_max]:
        // a negative value's scale position clamped to 0, so the bar's
        // width collapsed to 0 and it never drew at all.
        let payload = make_payload(make_conditional_horizontal_payload(false));
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        let rects = parse_rects(&svg);
        assert_eq!(rects.len(), 2, "both the positive and negative bar draw: {rects:?}");

        let zero_x = zero_rule_x(&svg);
        let positive = rects
            .iter()
            .find(|(x, _, _, _, _)| (*x - zero_x).abs() < 0.5)
            .expect("positive bar starts at the zero rule");
        let negative = rects
            .iter()
            .find(|(x, _, w, _, _)| (*x + *w - zero_x).abs() < 0.5)
            .expect("negative bar ends at the zero rule");
        assert!(positive.2 > 0.5, "positive bar has width");
        assert!(negative.0 < zero_x, "negative bar starts left of the zero rule");
    }

    #[test]
    fn horizontal_bar_value_labels_take_the_narrative_register() {
        let payload = make_payload(make_conditional_horizontal_payload(false));
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        assert!(svg.contains(">2mn<"), "positive label narrative-formatted: {svg}");
        assert!(svg.contains(">\u{2212}900k<"), "negative label narrative-formatted: {svg}");
        assert!(!svg.contains(">2M<"), "raw d3 SI suffix must not leak through");
    }

    /// The labels the production painter prints for `arr = 2000000, -900000`
    /// under each resolved format (`painted_label_text`,
    /// `render/chart/features/value_labels.py`); `label_is_house` is the
    /// payload's `resolve_label_format` verdict.
    fn value_label_svg(format: serde_json::Value, house: bool) -> Result<String, String> {
        let mut json = make_conditional_horizontal_payload(false);
        json["resolved"]["style"]["mark"]["labels"]["format"] = format;
        json["resolved"]["style"]["label_is_house"] = serde_json::json!(house);
        render(&make_payload(json), &test_fonts()).map_err(|e| e.to_string())
    }

    #[test]
    fn a_house_value_label_paints_its_prefix_after_the_narrative_register() {
        let svg = value_label_svg(
            serde_json::json!({"spec": ".3~s", "prefix": "CHF ", "raw": "number"}),
            true,
        )
        .expect("renders");
        assert!(svg.contains(">CHF 2mn<"), "{svg}");
        assert!(svg.contains(">CHF \u{2212}900k<"), "a spaced prefix leads the sign: {svg}");
    }

    #[test]
    fn a_literal_spec_value_label_keeps_d3s_letters_inside_its_prefix() {
        let svg = value_label_svg(
            serde_json::json!({"spec": ".3~s", "prefix": "CHF ", "raw": ".3~s"}),
            false,
        )
        .expect("renders");
        assert!(svg.contains(">CHF 2M<"), "{svg}");
        assert!(svg.contains(">CHF \u{2212}900k<"), "{svg}");
    }

    #[test]
    fn a_value_label_suffix_trails_the_digits() {
        let svg = value_label_svg(
            serde_json::json!({"spec": ",.0f", "suffix": " CHF", "raw": "integer"}),
            false,
        )
        .expect("renders");
        assert!(svg.contains(">2,000,000 CHF<"), "{svg}");
        assert!(svg.contains(">\u{2212}900,000 CHF<"), "{svg}");
    }

    #[test]
    fn an_authored_analytic_notation_glues_the_word_to_a_value_labels_digits() {
        let svg = value_label_svg(
            serde_json::json!({"spec": ".3~s", "prefix": "US$", "notation": "analytic", "raw": "number"}),
            true,
        )
        .expect("renders");
        assert!(svg.contains(">US$2M<"), "{svg}");
        assert!(svg.contains(">\u{2212}US$900K<"), "a glued prefix trails the sign: {svg}");
    }

    #[test]
    fn a_value_label_sign_placement_the_author_chose_wins_over_the_prefix_default() {
        let svg = value_label_svg(
            serde_json::json!({"spec": ",.0f", "prefix": "US$", "sign_placement": "after_prefix", "raw": "integer"}),
            false,
        )
        .expect("renders");
        assert!(svg.contains(">US$\u{2212}900,000<"), "{svg}");
    }

    #[test]
    fn horizontal_bar_without_conditional_channel_keeps_the_single_series_fill() {
        let payload = make_payload(make_conditional_horizontal_payload(false));
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        let rects = parse_rects(&svg);
        assert_eq!(rects.len(), 2);
        assert!(
            rects.iter().all(|(_, _, _, _, fill)| fill == "#1c395c"),
            "both bars keep single_series_fill without a conditional channel: {rects:?}"
        );
    }

    #[test]
    fn horizontal_bar_negative_bar_rounds_its_left_end() {
        // The far-end corner radius flips with sign: a positive bar rounds
        // its right end, a negative bar its left end.
        let mut payload_json = make_conditional_horizontal_payload(false);
        payload_json["resolved"]["style"]["mark"]["border"]["radius"] = serde_json::json!(2.0);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        // Horizontal single-series bars draw value-descending (Expansion
        // 2M before Churn −900k), so the first rounded-rect path is the
        // positive bar and the second is the negative one.
        let bar_paths: Vec<&str> = svg
            .match_indices("<path d=\"M ")
            .map(|(i, _)| svg[i..].split('>').next().unwrap())
            .filter(|p| p.contains(" A "))
            .collect();
        assert_eq!(bar_paths.len(), 2, "both bars draw as rounded paths: {bar_paths:?}");
        // Positive bar (round-right): "M x,y H ... A" — the H command comes
        // before the first arc.
        let positive_second_cmd = bar_paths[0].split(' ').nth(3).unwrap();
        assert_eq!(positive_second_cmd, "H", "positive bar rounds its right (far) end: {}", bar_paths[0]);
        // Negative bar (round-left): "M x,y A ..." — the arc comes first.
        let negative_second_cmd = bar_paths[1].split(' ').nth(3).unwrap();
        assert_eq!(negative_second_cmd, "A", "negative bar rounds its left (far) end: {}", bar_paths[1]);
    }

    /// Production's `autosize: fit` unions every mark's bounds into the
    /// plot-rect negotiation, including value-label TEXT marks — a fitted
    /// horizontal bar's right margin is exactly the rightmost positive
    /// bar's own label overhang (`arr_chart`: "2mn" starts at
    /// `x(2M) + dx 4` and its measured advance carries past the plot,
    /// reserving production's 2px right margin). Before porting that ink
    /// into the fit closure, the label painted with NO reservation at all
    /// and clipped past the card's own padding boundary.
    #[test]
    fn horizontal_bar_value_label_ink_reserves_the_right_margin() {
        let payload = make_payload(make_conditional_horizontal_payload(false));
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        let marker = "fill=\"#F7B068\">";
        let start = svg.find(marker).expect("a value label paints in its font color");
        let text_start = start + marker.len();
        let text_end = svg[text_start..].find('<').unwrap() + text_start;
        let text = &svg[text_start..text_end];
        assert_eq!(text, "2mn", "the positive (Expansion) bar's label is the rightmost ink");
        let tag_start = svg[..start].rfind("<text").unwrap();
        let tag = &svg[tag_start..start];
        let x_start = tag.find("x=\"").unwrap() + 3;
        let x_end = tag[x_start..].find('"').unwrap() + x_start;
        let x: f64 = tag[x_start..x_end].parse().unwrap();
        let width = fonts.measure(text, TEST_FONT, 11.0).unwrap();

        // Card's own padding boundary — payload.width - padding.right.
        let card_right_edge = 600.0 - 16.0;
        assert!(
            x + width <= card_right_edge + 0.5,
            "value label overhangs the card's own right padding boundary: \
             x={x} + width={width} = {} > {card_right_edge}",
            x + width
        );
    }

    /// An UNBAKED measure axis (no computed tick ladder) has no rung to pin a
    /// negative floor from: Vega's `zero: true` extent is the data's own.
    #[test]
    fn horizontal_bar_negative_value_on_an_unbaked_axis_draws_to_the_data_floor() {
        use serde_json::json;
        let mut payload_json = make_horizontal_payload();
        payload_json["resolved"]["style"]["axis_y"]["tick_values"] = json!([]);
        payload_json["resolved"]["style"]["axis_y"]["domain_max"] = json!(null);
        payload_json["rows"][0]["revenue"] = json!(-800.0);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("a negative value on an unbaked axis draws");
        assert!(svg.contains('\u{2212}'), "the data floor is on the axis: {svg}");
    }

    /// The horizontal arm draws the subtitle production draws.
    ///
    /// Production compiles one title BLOCK for both orientations — title
    /// plus subtitle, `resolve/chart/bar.py` setting `subtitle_present`
    /// with no orientation branch. This arm reserved a single-line band and
    /// emitted only the title, so every subtitled horizontal chart sat one
    /// subtitle band too high with its second line missing. Measured on the
    /// four `dundersign-support-operations` `bar_*` charts at 8.2-17.7%
    /// diff_frac, the gap `libs/chart-svg/AGENTS.md` recorded when the
    /// title tiers landed.
    #[test]
    fn horizontal_arm_draws_and_reserves_the_subtitle() {
        use serde_json::json;
        let build = |subtitle: Option<&str>| {
            let mut p = make_horizontal_payload();
            p["resolved"]["title"] = json!("Revenue by product");
            p["resolved"]["title_style"] = json!({
                "font": {"family": TEST_FONT, "size": 18.0, "weight": 500.0, "case": "none"},
                "position": {"offset": 10.0},
                // 13, deliberately NOT the `unwrap_or(11.0)` fallback: the
                // band assertion below then pins the authored-size READ, not
                // just the reservation.
                "subtitle": {"font": {"family": TEST_FONT, "size": 13.0, "weight": "400"}}
            });
            if let Some(t) = subtitle {
                p["resolved"]["subtitle"] = json!(t);
            }
            make_payload(p)
        };
        let fonts = test_fonts();
        let with_sub = render(&build(Some("Q3 FY25")), &fonts).expect("renders");
        let without = render(&build(None), &fonts).expect("renders");

        assert!(with_sub.contains(">Q3 FY25<"), "the subtitle paints: {with_sub}");

        // …and it reserves its own band: the plot drops by the subtitle
        // line plus Vega's subtitlePadding, exactly as the vertical arm's
        // `TitleInk::with_subtitle` accounts for it.
        let plot_top = |s: &str| -> f64 {
            let i = s.find("<g transform=\"translate(").expect("plot group");
            s[i..].split(',').nth(1).unwrap().split(')').next().unwrap().trim().parse().unwrap()
        };
        let delta = plot_top(&with_sub) - plot_top(&without);
        let want = 13.0 + crate::constants::layout::VEGA_SUBTITLE_PADDING;
        assert_eq!(
            delta, want,
            "the subtitle band is reserved: plot moved {delta}, expected {want}"
        );
    }

    /// A horizontal bar with a color channel keeps the query's category
    /// order.
    ///
    /// Production's `_emit_horizontal` pins `sort: null` on a
    /// series-colored (or wide) horizontal bar, so Vega-Lite keeps
    /// first-occurrence row order rather than its default ascending sort.
    /// This arm ordered every colored domain alphabetically — right under
    /// the old default, and 33pp wrong on
    /// `charts/bar-charts_4/category_columns_styled` once production
    /// changed: the reference draws Electronics, Accessories, Tools (query
    /// order) where this drew Accessories, Electronics, Tools. The
    /// vertical arm had the same alphabetical sort and loses it in the same
    /// change (`vertical_colored_bar_keeps_query_order`).
    #[test]
    fn horizontal_colored_bar_keeps_query_order() {
        use serde_json::json;
        let mut p = make_horizontal_payload();
        // Color by the category itself, as `category_columns_styled` does,
        // with rows deliberately out of alphabetical order.
        p["resolved"]["color"] = json!("product");
        p["resolved"]["palette"] = json!(["#3164a3", "#779bc9", "#ad9c7f"]);
        p["rows"] = json!([
            {"product": "Zebra", "revenue": 800.0},
            {"product": "Apple", "revenue": 600.0},
            {"product": "Mango", "revenue": 700.0}
        ]);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let pos = |name: &str| svg.find(&format!(">{name}<")).unwrap_or_else(|| panic!("{name} label missing: {svg}"));
        let (z, a, m) = (pos("Zebra"), pos("Apple"), pos("Mango"));
        assert!(
            z < a && a < m,
            "query order Zebra, Apple, Mango must survive: Zebra@{z} Apple@{a} Mango@{m}"
        );
    }

    /// A vertical bar with a color channel keeps the query's category
    /// order too — production pins `sort: null` on BOTH orientations.
    ///
    /// `_emit_vertical` spreads `chart_sort_to_vl(chart.sort)` straight
    /// into the x encoding, and that is `None` when nothing is authored —
    /// a literal `"sort": null`, which `_cartesian.py` documents as
    /// "disables VL's default alphabetical ordering so the query's row
    /// order is preserved". This arm sorted a colored domain
    /// alphabetically. Of the corpus's two occupants of that cell,
    /// `charts/bar-charts_7/region_revenue_by_category_vertical` (an
    /// authored `orientation: vertical`, nominal x, color by category)
    /// cannot tell — it happens to `ORDER BY region`, so query order and
    /// alphabetical coincide — and
    /// `fixtures/stacked-bar-total-label-vertical/revenue_chart` can: it
    /// moved 10.03% MISMATCH → 0.46% MATCH with this change. Rows
    /// out of alphabetical order are what this test supplies as the unit
    /// guard.
    #[test]
    fn vertical_colored_bar_keeps_query_order() {
        let payload = make_payload(make_vertical_payload(
            &[("Zebra", 10.0), ("Apple", 20.0), ("Mango", 15.0)],
            true,
        ));
        let svg = render(&payload, &test_fonts()).expect("renders");
        let pos = |name: &str| svg.find(&format!(">{name}<")).unwrap_or_else(|| panic!("{name} label missing: {svg}"));
        let (z, a, m) = (pos("Zebra"), pos("Apple"), pos("Mango"));
        assert!(
            z < a && a < m,
            "query order Zebra, Apple, Mango must survive: Zebra@{z} Apple@{a} Mango@{m}"
        );
    }

    /// An authored `chart.sort` orders the domain by the summed sort field,
    /// on both orientations — production's `stacked_x_domain_order`
    /// (`utils.py`): `_emit_horizontal` lets an authored sort win over its
    /// value-descending default, `_emit_vertical` spreads it into the
    /// encoding, and Vega-Lite's `EncodingSortField.op` defaults to `sum`.
    /// A category with no value for the sort field keeps its
    /// first-occurrence position after the ranked ones.
    ///
    /// The rows are chosen so query order (Mango, Zebra, Apple), alphabetical
    /// (Apple, Mango, Zebra), value-descending (Apple, Mango, Zebra) and the
    /// authored order (Zebra, Mango, Apple) are all distinguishable.
    /// `playground/dundersign-support-operations/hero_queue_age` is the
    /// corpus case: `sort: {by: age_order, order: asc}` on rank-prefixed
    /// labels, which alphabetical order happened to reproduce until the
    /// alphabetical arm was removed.
    #[test]
    fn horizontal_bar_honors_authored_sort() {
        use serde_json::json;
        let mut p = make_horizontal_payload();
        p["resolved"]["sort"] = json!({"by": "rank", "order": "asc"});
        p["rows"] = json!([
            {"product": "Mango", "revenue": 700.0, "rank": 2},
            {"product": "Zebra", "revenue": 600.0, "rank": 1},
            {"product": "Apple", "revenue": 800.0, "rank": 3},
            {"product": "Kiwi", "revenue": 100.0, "rank": null}
        ]);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        // Vega sorts the bar's domain itself: a category with no rank leads ascending.
        assert_label_order(&svg, &["Kiwi", "Zebra", "Mango", "Apple"]);
    }

    #[test]
    fn vertical_bar_honors_authored_sort() {
        use serde_json::json;
        let mut p = make_vertical_payload(&[("Mango", 700.0), ("Zebra", 600.0), ("Apple", 800.0)], true);
        p["resolved"]["sort"] = json!({"by": "rank", "order": "desc"});
        p["rows"] = json!([
            {"product": "Mango", "region": "North", "revenue": 700.0, "rank": 2},
            {"product": "Mango", "region": "South", "revenue": 400.0, "rank": 2},
            {"product": "Zebra", "region": "North", "revenue": 600.0, "rank": 1},
            {"product": "Zebra", "region": "South", "revenue": 300.0, "rank": 1},
            {"product": "Apple", "region": "North", "revenue": 800.0, "rank": 3},
            {"product": "Apple", "region": "South", "revenue": 500.0, "rank": 3}
        ]);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert_label_order(&svg, &["Apple", "Mango", "Zebra"]);
    }

    /// A colored vertical bar over rows where `Sum` and `Min` rank the
    /// categories oppositely on both sort fields: by `revenue` (Alpha 100+100
    /// = 200, min 100; Beta 150+90 = 240, min 90) and by `weight` (Alpha 1+9
    /// = 10, min 1; Beta 4+4 = 8, min 4).
    fn sum_min_payload(stack: &str, sort_by: &str) -> serde_json::Value {
        use serde_json::json;
        let mut p = make_vertical_payload(&[("Alpha", 0.0)], true);
        p["resolved"]["stack"] = json!(stack);
        p["resolved"]["stacked_domain_max"] = json!(300.0);
        p["resolved"]["sort"] = json!({"by": sort_by, "order": "asc"});
        p["rows"] = json!([
            {"product": "Alpha", "region": "North", "revenue": 100.0, "weight": 1.0},
            {"product": "Alpha", "region": "South", "revenue": 100.0, "weight": 9.0},
            {"product": "Beta", "region": "North", "revenue": 150.0, "weight": 4.0},
            {"product": "Beta", "region": "South", "revenue": 90.0, "weight": 4.0}
        ]);
        p
    }

    fn sum_min_order(stack: &str, sort_by: &str) -> Vec<&'static str> {
        let svg = render(&make_payload(sum_min_payload(stack, sort_by)), &test_fonts())
            .expect("renders");
        let alpha = svg.find(">Alpha<").expect("Alpha label");
        let beta = svg.find(">Beta<").expect("Beta label");
        if alpha < beta { vec!["Alpha", "Beta"] } else { vec!["Beta", "Alpha"] }
    }

    /// `bar_sort_op`'s one `sum` case: a stacked bar sorted by its own
    /// measure ranks by the stacked total, not by any one segment.
    #[test]
    fn stacked_bar_sorted_by_its_own_measure_ranks_by_the_stacked_total() {
        assert_eq!(sum_min_order("zero", "revenue"), ["Alpha", "Beta"]);
    }

    /// Every other sort on a stacked bar folds `min` — the sort column's own
    /// value, which a well-formed column repeats across a category.
    #[test]
    fn stacked_bar_sorted_by_another_field_folds_min() {
        assert_eq!(sum_min_order("zero", "weight"), ["Alpha", "Beta"]);
    }

    /// An unstacked (grouped) bar never totals, even by its own measure.
    #[test]
    fn grouped_bar_sorted_by_its_own_measure_folds_min() {
        assert_eq!(sum_min_order("none", "revenue"), ["Beta", "Alpha"]);
    }

    #[test]
    fn grouped_bar_sorted_by_another_field_folds_min() {
        assert_eq!(sum_min_order("none", "weight"), ["Alpha", "Beta"]);
    }

    /// Bar's sort is Vega's own: a `min` key compares as JavaScript does, so
    /// the text "1" beside a 9 keeps the min "1" (`9 < "1"` is false) and ranks
    /// below Beta's 4, as vl_convert draws it.
    #[test]
    fn min_sort_over_numbers_and_text_compares_as_javascript() {
        let mut p = sum_min_payload("none", "weight");
        p["resolved"]["sort"]["order"] = serde_json::json!("desc");
        p["rows"][0]["weight"] = serde_json::json!("1");
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert_label_order(&svg, &["Beta", "Alpha"]);
    }

    /// A text key JavaScript cannot order against a number is equal to it, so
    /// the rows' own order stands under either direction.
    #[test]
    fn authored_sort_over_numbers_and_unorderable_text_keeps_row_order() {
        use serde_json::json;
        let mut p = make_horizontal_payload();
        p["resolved"]["sort"] = json!({"by": "rank", "order": "asc"});
        p["rows"] = json!([
            {"product": "Mango", "revenue": 600.0, "rank": 2},
            {"product": "Zebra", "revenue": 700.0, "rank": "first"}
        ]);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert_label_order(&svg, &["Mango", "Zebra"]);
    }

    fn assert_label_order(svg: &str, names: &[&str]) {
        let pos: Vec<usize> = names
            .iter()
            .map(|n| svg.find(&format!(">{n}<")).unwrap_or_else(|| panic!("{n} label missing: {svg}")))
            .collect();
        assert!(pos.windows(2).all(|w| w[0] < w[1]), "expected {names:?}, positions {pos:?}");
    }

    /// `render_horizontal` reads the tiered slot too — the arm whose gate
    /// round 1 required be dropped, and the one the corpus cannot police.
    ///
    /// The four `dundersign-support-operations` `bar_*` charts route here.
    /// When this test was written they stayed MISMATCH whether the arm
    /// tiered or not, so only their diff_frac moved and the status ratchet
    /// could not see a re-gate — this test was the sole guard. Porting the
    /// horizontal subtitle changed that: those four now sit at 3 MATCH + 1
    /// CLOSE, so re-gating drops them to 4 MISMATCH and the ratchet does
    /// catch it. Kept as the unit-level guard, and because the ratchet
    /// polices the pair rather than this binding alone.
    #[test]
    fn horizontal_arm_reads_the_tiered_title_font() {
        use serde_json::json;
        let build = |tiered: bool| {
            let mut p = make_horizontal_payload();
            p["resolved"]["title"] = json!("Revenue by product");
            p["resolved"]["title_style"] = json!({
                "font": {"family": TEST_FONT, "size": 18.0, "weight": 500.0, "case": "none"},
                "position": {"offset": 10.0},
                "subtitle": {"font": {"family": TEST_FONT, "size": 11.0, "weight": "400"}}
            });
            // A SUBTITLE is what makes this cover the dropped gate rather
            // than its complement: the gate keyed on `subtitle.is_some()`,
            // and all four charts the gate held carry one. Without this the
            // gate never fires and re-introducing it fails nothing.
            p["resolved"]["subtitle"] = json!("Q3 FY25");
            if tiered {
                p["resolved"]["style"]["title_font"] = json!({
                    "family": TEST_FONT, "size": 11.0, "weight": "600", "color": "#222222"
                });
            }
            make_payload(p)
        };
        let fonts = test_fonts();
        let svg = render(&build(true), &fonts).expect("renders");
        let untiered = render(&build(false), &fonts).expect("renders");

        let tag = |s: &str| -> String {
            let pos = s.find(">Revenue by product<").expect("title present");
            s[s[..pos].rfind("<text").unwrap()..pos].to_string()
        };
        assert!(tag(&svg).contains("font-size=\"11\""), "tiered size wins: {}", tag(&svg));
        assert!(tag(&svg).contains("font-weight=\"600\""), "tiered weight wins: {}", tag(&svg));
        assert!(tag(&untiered).contains("font-size=\"18\""), "…and 18px without it");

        // The band it reserves shrinks with it, so the plot sits higher.
        let plot_top = |s: &str| -> f64 {
            let i = s.find("<g transform=\"translate(").expect("plot group");
            s[i..].split(',').nth(1).unwrap().split(')').next().unwrap().trim().parse().unwrap()
        };
        assert!(
            plot_top(&svg) < plot_top(&untiered),
            "the 11px title reserves a smaller band ({}) than the 18px one ({})",
            plot_top(&svg),
            plot_top(&untiered)
        );
    }

    /// A stacked vertical bar with visible endpoint labels lands
    /// `render_vertical_panel`'s TRUE legacy `else` only when it clears
    /// BOTH the fit arm's own gate (`fit_gate = !(is_stacked &&
    /// endpoint_labels.visible) && support_table.is_none()` — `is_stacked
    /// && visible` here keeps it off the fit arm) AND `rail_concat`'s
    /// (`!val_on_right` — a value axis pinned `axis_y.position: "right"`
    /// keeps it off the rail arm regardless of tick_values). An earlier
    /// version of this test used an UNBAKED value axis (no `tick_values`)
    /// as its only distinguishing shape — that was `rail_concat`'s own
    /// discriminator before the gate-widening fix admitted an unbaked
    /// axis too (production's real rail condition,
    /// `emitters/_endpoint_rail.py:44-77`, never reads `tick_values` at
    /// all), so that shape now renders through the rail arm instead and
    /// the old test could no longer see the legacy arm's own subtitle
    /// paint regress. `axis_y.position: "right"` is the shape that stays
    /// legacy regardless of that gate's own tick-value admission.
    ///
    /// That arm's own title reservation, `title_band = title_fs + 10.0`,
    /// only ever accounted for a single title line and always left
    /// `title_pos: None` and `legend: None` on the `PlotRect` it built —
    /// the SAME single-line-only law `horizontal_arm_draws_and_reserves_the_
    /// subtitle`'s own doc comment already names as fixed on the
    /// horizontal arm — that fix predates this branch, already on main.
    /// The paint site's `match
    /// (vega_title_pos, ...)` falls to its `_` arm on a `None` title pos and
    /// now paints the subtitle too when `sub_lines` is non-empty.
    ///
    /// Two-state mutation this test exists to catch: delete the legacy
    /// arm's subtitle paint (or revert its `title_band` to the
    /// old flat `title_fs + 10.0` regardless of subtitle) and this test
    /// must FAIL — verified by hand against both mutations before landing.
    /// Also pins `emit_title`'s own top-baseline law (its `title_y`
    /// assertion below).
    #[test]
    fn vertical_legacy_arm_right_axis_draws_and_reserves_the_subtitle() {
        use serde_json::json;
        let build = |subtitle: Option<&str>| {
            let mut p = make_stacked_payload("vertical", "zero");
            p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
            p["resolved"]["style"]["axis_y"]["position"] = json!("right");
            // `show_rail_v` (is_stacked && visible && color) is true
            // regardless of which arm ultimately renders — the rail-pane
            // width probe it gates runs unconditionally, before
            // `rail_concat`'s own further exclusions are even checked.
            p["resolved"]["style"]["series_label"]["gap_px"] = json!(8.0);
            p["resolved"]["title_style"]["subtitle"] =
                json!({"font": {"family": TEST_FONT, "size": 18.0, "weight": "400"}});
            if let Some(t) = subtitle {
                p["resolved"]["subtitle"] = json!(t);
            }
            make_payload(p)
        };
        let fonts = test_fonts();
        let with_sub = render(&build(Some("Q3 FY25")), &fonts).expect("renders");
        let without = render(&build(None), &fonts).expect("renders");

        assert!(with_sub.contains(">Q3 FY25<"), "the subtitle paints: {with_sub}");

        let plot_top = |s: &str| -> f64 {
            let i = s.find("<g transform=\"translate(").expect("plot group");
            s[i..].split(',').nth(1).unwrap().split(')').next().unwrap().trim().parse().unwrap()
        };
        let delta = plot_top(&with_sub) - plot_top(&without);
        // The `subtitle.font.size` override above (18.0) matches the
        // title's own un-tiered size (`title_measure_font_size`'s
        // fallback) — the same 18px this fixture's title uses — so the
        // override doesn't change the expected reservation math below.
        let want = 18.0 + crate::constants::layout::VEGA_SUBTITLE_PADDING;
        assert_eq!(
            delta, want,
            "the subtitle band is reserved: plot moved {delta}, expected {want}"
        );

        // This arm's title paint (`common::emit_title`) must land at
        // `pad_t + baseline_shift("top", size)`. `pad_t` here is this
        // fixture's own padding.top (16.0); title size is this fixture's
        // un-tiered 18.0 (`make_stacked_payload`'s title_style).
        let title_y = |s: &str| -> f64 {
            let i = s.find(">Stacked Test<").expect("title text present");
            let start = s[..i].rfind(" y=\"").expect("title y attr");
            let end = s[start + 4..].find('"').expect("y attr close quote") + start + 4;
            s[start + 4..end].parse().unwrap()
        };
        let pad_t = 16.0;
        assert_eq!(
            title_y(&without),
            pad_t + measure::baseline_shift("top", 18.0),
            "title baseline must use Vega's real top-baseline shift, not the 0.85 approximation: {without}"
        );
    }

    /// The rail arm's margins take label ink: a value label leaving the plot grows
    /// the pane's bounds, and a pane the ink outgrows is the concat's overshoot error.
    #[test]
    fn vertical_rail_arm_counts_a_value_label_that_leaves_the_plot_in_the_pane_overshoot() {
        use serde_json::json;
        let mut p = make_stacked_payload("vertical", "zero");
        p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
        p["resolved"]["style"]["mark"]["labels"] = json!({
            "visible": true, "position": "above", "dx": 2000.0,
            "field": null, "format": null, "dy": null, "font": null
        });
        p["resolved"]["style"]["label_font_size"] = json!(11.0);
        p["resolved"]["style"]["series_label"]["gap_px"] = json!(8.0);
        let err = render(&make_payload(p), &test_fonts()).unwrap_err();
        assert!(format!("{err:?}").contains("ERR-CONCAT-OVERSHOOT-NONPOSITIVE"), "{err:?}");
    }

    /// An unbaked, uncounted value axis on the rail arm takes Vega-Lite's
    /// default `tickCount = ceil(height/40)` at the pane's final height
    /// (`unbaked_rungs`), as the horizontal and fit arms already do —
    /// `niced_rail_domain`'s count-10 ladder is provisional. A 120 max in
    /// a ~270px pane gives count 7 → d3 step 20 (7 rungs); count 10 gave
    /// step 10 (13 rungs) — production's `hero_queue_age` draws 7.
    #[test]
    fn vertical_rail_arm_unbaked_ladder_takes_vega_lite_default_tick_count() {
        use serde_json::json;
        let mut p = make_stacked_payload("vertical", "zero");
        p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
        p["resolved"]["style"]["series_label"]["gap_px"] = json!(8.0);
        p["resolved"]["style"]["axis_y"]["tick_values"] = json!([]);
        p["resolved"]["style"]["axis_y"]["ticks"]["count"] = json!(null);
        p["resolved"]["stacked_domain_max"] = json!(120.0);
        p["height"] = json!(340.0);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let labels: Vec<&str> = svg
            .split("<text")
            .filter(|t| t.contains("text-anchor=\"end\""))
            .filter_map(|t| t.split('>').nth(1).and_then(|r| r.split('<').next()))
            .filter(|l| l.chars().all(|c| c.is_ascii_digit()))
            .collect();
        assert_eq!(labels, ["0", "20", "40", "60", "80", "100", "120"], "{svg}");
    }

    /// A flip-aligned right value axis sizes its gutter (production's baked
    /// `labelPadding`) from the DRAWN ladder — `axis.drawn_tick_values`, the
    /// baked rungs inside the baked domain. A baked rung past the baked
    /// `domain_max` is never drawn and never sizes the gutter.
    #[test]
    fn right_value_gutter_measures_the_drawn_ladder_not_a_rung_past_the_domain() {
        use serde_json::json;
        let render_with = |ladder: serde_json::Value| {
            let mut p = make_stacked_payload("vertical", "zero");
            p["resolved"]["style"]["axis_y"]["position"] = json!("right");
            p["resolved"]["style"]["axis_y"]["domain_max"] = json!(50.0);
            p["resolved"]["style"]["axis_y"]["tick_values"] = ladder;
            render(&make_payload(p), &test_fonts()).expect("renders")
        };
        let label_x = |svg: &str, text: &str| -> f64 {
            let i = svg.find(&format!(">{text}</text>")).expect("label draws");
            let tag = &svg[svg[..i].rfind("<text").unwrap()..i];
            tag.split("x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        let drawn = render_with(json!([0.0, 25.0, 50.0]));
        let with_far_rung = render_with(json!([0.0, 25.0, 50.0, 5_000_000.0]));
        assert!(!with_far_rung.contains(">5,000,000<"), "the rung past the domain is not drawn: {with_far_rung}");
        assert_eq!(
            label_x(&with_far_rung, "50"),
            label_x(&drawn, "50"),
            "the undrawn rung must not size the gutter: {with_far_rung}"
        );
    }

    /// The continuous-x arm sizes its right value gutter by the same law:
    /// the drawn ladder, never a baked rung past the baked `domain_max`.
    #[test]
    fn continuous_x_right_value_gutter_measures_the_drawn_ladder() {
        use serde_json::json;
        let xs: Vec<(f64, f64)> = (0..24).map(|h| (h as f64, 100.0 + h as f64)).collect();
        let render_with = |ladder: serde_json::Value| {
            let mut p = make_continuous_payload(&xs);
            p["resolved"]["style"]["axis_y"]["domain_max"] = json!(300.0);
            p["resolved"]["style"]["axis_y"]["tick_values"] = ladder;
            render(&make_payload(p), &test_fonts()).expect("renders")
        };
        let label_x = |svg: &str, text: &str| -> f64 {
            let i = svg.find(&format!(">{text}</text>")).expect("label draws");
            let tag = &svg[svg[..i].rfind("<text").unwrap()..i];
            tag.split("x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        let drawn = render_with(json!([0.0, 100.0, 200.0, 300.0]));
        let with_far_rung = render_with(json!([0.0, 100.0, 200.0, 300.0, 5_000_000.0]));
        assert!(!with_far_rung.contains(">5,000,000<"), "{with_far_rung}");
        // The right labels sit at the plot's right edge plus the gutter, so a
        // wider gutter barely moves them; the x ladder's last rung moves by
        // the plot width the gutter would have taken.
        assert_eq!(
            label_x(&with_far_rung, "20"),
            label_x(&drawn, "20"),
            "the undrawn rung must not size the gutter: {with_far_rung}"
        );
        assert_eq!(label_x(&with_far_rung, "300"), label_x(&drawn, "300"));
    }

    #[test]
    fn vertical_single_series_sort_preserves_insertion_order() {
        // VL nominal axis with no explicit sort preserves data insertion order.
        // The query layer (SQL ORDER BY) controls the render order; Rust mirrors that.
        // Cats in insertion order: Zebra, Apple, Mango — Rust must preserve this.
        use serde_json::json;
        let mut payload_json = make_vertical_payload(
            &[("Zebra", 1800.0), ("Apple", 900.0), ("Mango", 1200.0)],
            false,
        );
        payload_json["resolved"]["style"]["mark"]["border"]["radius"] = json!(0.0);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        let zebra_pos = svg.find(">Zebra<").expect("Zebra label in SVG");
        let apple_pos = svg.find(">Apple<").expect("Apple label in SVG");
        let mango_pos = svg.find(">Mango<").expect("Mango label in SVG");
        assert!(
            zebra_pos < apple_pos && apple_pos < mango_pos,
            "vertical labels preserve insertion order: Zebra({zebra_pos}) < Apple({apple_pos}) < Mango({mango_pos})"
        );
    }

    /// An authored `timeFormat(datum.value, F)` on a time axis speaks `F` of every tick's instant.
    #[test]
    fn a_time_format_label_expr_speaks_its_format_at_every_tick() {
        let _locale = crate::time_locale::en_us_scope();
        let mut p = make_time_payload(&months_from_2000(72));
        p["resolved"]["style"]["axis_x"]["labels"]["expr"] = serde_json::json!("timeFormat(datum.value, '%m/%y')");
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(svg.contains(">01/05</text>") && !svg.contains(">2005</text>"), "{svg}");
    }

    /// The expression evaluator composes builtins: an upper-cased, concatenated date text.
    #[test]
    fn a_composed_label_expr_evaluates_at_every_tick() {
        let _locale = crate::time_locale::en_us_scope();
        let mut p = make_time_payload(&months_from_2000(72));
        p["resolved"]["style"]["axis_x"]["labels"]["expr"] =
            serde_json::json!("'Q' + (quarter(datum.value) + 1) + upper(timeFormat(datum.value, ' %y'))");
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(svg.contains(">Q2 05</text>"), "Vega's quarter() counts from 1, so January plus one is Q2: {svg}");
    }

    /// An axis expression that reads the plot's scales paints them at the size the layout settles on: the second
    /// band starts halfway across what `range('x')` names as the plot's width.
    #[test]
    fn a_scale_reading_expr_paints_the_final_plot() {
        let labels = |expr: &str| -> Vec<String> {
            let mut p = make_vertical_payload(&[("a", 10.0), ("b", 20.0)], false);
            p["resolved"]["style"]["axis_x"]["labels"]["expr"] = serde_json::json!(expr);
            let (svg, _) = crate::render_sized(&make_payload(p), &test_fonts()).expect("renders");
            let mut found = Vec::new();
            for tag in svg.split("<text ").skip(1) {
                let text = tag.split('>').nth(1).unwrap_or("").split('<').next().unwrap_or("");
                if text.parse::<f64>().is_ok() && tag.contains("text-anchor=\"middle\"") {
                    found.push(text.to_string());
                }
            }
            found
        };
        let width: f64 = labels("range('x')[1] + ''")[0].parse().unwrap();
        let starts = labels("scale('x', datum.value) + ''");
        assert_eq!(starts[0], "0");
        assert_eq!(starts[1].parse::<f64>().unwrap(), width / 2.0, "{starts:?} over a plot {width} wide");
    }

    /// An array an expression returns paints a line per element, as Vega's `tspan`s.
    #[test]
    fn an_array_expr_paints_a_line_per_element() {
        let mut p = make_vertical_payload(&[("a", 10.0), ("b", 20.0)], false);
        p["resolved"]["style"]["axis_x"]["labels"]["expr"] = serde_json::json!("[datum.label, 'zz']");
        let (svg, _) = crate::render_sized(&make_payload(p), &test_fonts()).expect("renders");
        assert!(svg.contains("<tspan>a</tspan><tspan x=") && svg.contains("dy=\"13\">zz</tspan>"), "{svg}");
    }

    /// Vega evaluates the slice ternary on a time tick's number, which throws: nothing paints.
    #[test]
    fn the_slice_label_expr_on_a_time_axis_raises_painted_no_marks() {
        let mut p = make_time_payload(&months_from_2000(72));
        p["resolved"]["style"]["axis_x"]["labels"]["expr"] = serde_json::json!("datum.value ? slice(datum.value, 2) : ''");
        let err = render(&make_payload(p), &test_fonts()).expect_err("raises");
        assert_eq!(err.code, "ERR-CHART-PAINTED-NO-MARKS", "{err:?}");
    }

    /// A time format on a band axis over dates no grain explains labels every band with the formatted date.
    #[test]
    fn a_time_format_on_ungrained_band_dates_labels_each_band() {
        let dates = ["2024-01-01", "2024-01-29", "2024-02-26", "2024-03-25"];
        let cats: Vec<(&str, f64)> = dates.iter().map(|d| (*d, 20.0)).collect();
        let mut p = make_vertical_payload(&cats, false);
        p["resolved"]["style"]["axis_x"]["type"] = serde_json::json!("ordinal");
        p["resolved"]["style"]["axis_x"]["labels"]["format"] = serde_json::json!({"spec": "%d/%m/%Y"});
        p["resolved"]["style"]["label_usable_ratio"] = serde_json::json!(0.8);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(svg.contains(">29/01/2024</text>") && svg.contains(">25/03/2024</text>"), "{svg}");
    }

    /// B5 — authored `axis_x.labels.expr`: dundersign-support-operations'
    /// hero_queue_age strips a `"N · "` SQL ordering prefix
    /// (`datum.value ? slice(datum.value, 4) : ''`); the reference draws
    /// `≤7 days`/`8–30 days`, never the raw `"1 · ≤7 days"`.
    #[test]
    fn authored_labels_expr_rewrites_category_labels_before_drawing() {
        let _locale = crate::time_locale::en_us_scope();
        use serde_json::json;
        let mut payload_json =
            make_vertical_payload(&[("1 · ≤7 days", 800.0), ("2 · 8–30 days", 600.0)], false);
        payload_json["resolved"]["style"]["axis_x"]["labels"]["expr"] =
            json!("datum.value ? slice(datum.value, 4) : ''");
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        assert!(svg.contains(">≤7 days<"), "prefix stripped from the drawn label: {svg}");
        assert!(svg.contains(">8–30 days<"), "prefix stripped from the drawn label: {svg}");
        assert!(!svg.contains(">1 · "), "raw prefix must not reach the drawn SVG: {svg}");
        assert!(!svg.contains(">2 · "), "raw prefix must not reach the drawn SVG: {svg}");
    }

    /// The FIT ink for the non-temporal band arm must measure `cat_labels`
    /// (the `expr`-rewritten text), never raw `cat_domain` — the same
    /// invariant B6's `truncate_label` states for the drawn string. An
    /// UNCAPPED (`labels.max_width` large) 300-char raw category rewritten
    /// to `""` must render on a narrow canvas that a genuine 300-char label
    /// could never fit — measuring the raw string here would blow the
    /// width negotiation and refuse ("chart box too small"); measuring the
    /// rewritten empty string reserves nothing.
    #[test]
    fn vertical_bar_fit_ink_measures_the_rewritten_label_not_the_raw_one() {
        use serde_json::json;
        let mut payload_json =
            make_vertical_payload(&[("X".repeat(300).as_str(), 800.0), ("Beta", 600.0)], false);
        payload_json["resolved"]["style"]["axis_x"]["labels"]["expr"] =
            json!("datum.value ? slice(datum.value, 1000) : ''");
        payload_json["resolved"]["style"]["axis_x"]["labels"]["max_width"] = json!(5000.0);
        payload_json["width"] = json!(300.0);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        render(&payload, &fonts).expect(
            "a 300-char raw category rewritten to \"\" must not blow the fit's width \
             negotiation on a 300px canvas — it draws nothing, so it must reserve nothing",
        );
    }

    /// B5 on a calendar-grain (temporal) category axis rewrites the text of
    /// every bucket from the raw band value (`datum.value`), with no opener
    /// gate and no calendar vocabulary.
    #[test]
    fn labels_expr_on_a_calendar_grain_axis_rewrites_every_bucket() {
        use serde_json::json;
        let mut payload_json = make_vertical_payload(
            &[("2023-01-01", 800.0), ("2023-02-01", 600.0), ("2023-03-01", 500.0)],
            false,
        );
        payload_json["resolved"]["style"]["axis_x"]["labels"]["expr"] =
            json!("datum.value ? slice(datum.value, 2) : ''");
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        for label in ["23-01-01", "23-02-01", "23-03-01"] {
            assert!(svg.contains(&format!(">{label}<")), "{label}: {svg}");
        }
        assert!(!svg.contains(">Jan<"), "no calendar vocabulary: {svg}");
    }

    /// Any Vega expression rewrites a band's label: here a regex replace over `datum.label` and an upper case.
    #[test]
    fn a_composed_labels_expr_rewrites_band_labels() {
        use serde_json::json;
        let mut payload_json = make_vertical_payload(&[("Alpha-1", 800.0)], false);
        payload_json["resolved"]["style"]["axis_x"]["labels"]["expr"] =
            json!("upper(replace(datum.label, /-/g, ' '))");
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        assert!(svg.contains(">ALPHA 1<"), "{svg}");
    }

    /// `axis_x.labels.font.case` upper/lower cases the category labels of both orientations,
    /// composed over an authored `labels.expr`; any other case value leaves them as authored.
    #[test]
    fn category_label_font_case_applies_on_both_orientations() {
        use serde_json::json;
        let cased = |horizontal: bool, case: &str, expr: Option<&str>| {
            let mut payload_json = if horizontal {
                make_horizontal_payload()
            } else {
                make_vertical_payload(&[("Alpha one", 800.0), ("Beta Two", 600.0)], false)
            };
            payload_json["resolved"]["style"]["axis_x"]["labels"]["font"]["case"] = json!(case);
            if let Some(expr) = expr {
                payload_json["resolved"]["style"]["axis_x"]["labels"]["expr"] = json!(expr);
            }
            render(&make_payload(payload_json), &test_fonts()).expect("renders")
        };
        for horizontal in [false, true] {
            let (first, second) = if horizontal { ("Alpha", "Beta") } else { ("Alpha one", "Beta Two") };
            let svg = cased(horizontal, "upper", None);
            assert!(svg.contains(&format!(">{}<", first.to_uppercase())), "upper, horizontal={horizontal}: {svg}");
            assert!(svg.contains(&format!(">{}<", second.to_uppercase())), "upper, horizontal={horizontal}: {svg}");
            let svg = cased(horizontal, "lower", None);
            assert!(svg.contains(&format!(">{}<", second.to_lowercase())), "lower, horizontal={horizontal}: {svg}");
            let svg = cased(horizontal, "title", None);
            assert!(svg.contains(&format!(">{first}<")), "title leaves labels: horizontal={horizontal}: {svg}");
            let svg = cased(horizontal, "upper", Some("replace(datum.label, 'a', 'x')"));
            assert!(svg.contains(&format!(">{}<", first.replace('a', "x").to_uppercase())), "expr then upper, horizontal={horizontal}: {svg}");
        }
    }

    #[test]
    fn vertical_bar_measure_axis_on_right_places_labels_right_of_plot() {
        // axis_y.position = "right" (VL vertical-bar default) must draw value
        // tick labels to the right of the plot area, not the left.
        use serde_json::json;
        let mut payload_json = make_vertical_payload(
            &[("Alpha", 1800.0), ("Beta", 1200.0), ("Gamma", 900.0)],
            false,
        );
        payload_json["resolved"]["style"]["axis_y"]["position"] = json!("right");
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        // Value tick label (right-aligned to the measured gutter's far edge
        // under the vega-fit layout) must sit to the right of the plot
        // center — a right-positioned value axis moves labels off the left rail.
        let tag_start = svg
            .find("text-anchor=\"end\"")
            .and_then(|pos| svg[..pos].rfind("<text"))
            .expect("value-axis label <text> tag found");
        let tag = &svg[tag_start..tag_start + 200];
        let val_label_x: f64 = tag
            .split("x=\"")
            .nth(1)
            .and_then(|s| s.split('"').next())
            .expect("x attr present")
            .parse()
            .expect("x is numeric");

        // The category-label anchor text-anchor="middle" band centers are all
        // well left of the canvas width; a right-positioned value axis must
        // place its label near the right edge (> half the canvas width).
        assert!(
            val_label_x > payload.width / 2.0,
            "value-axis label x={val_label_x} should be right of plot center on axis_y.position=right"
        );
    }

    #[test]
    fn vertical_bar_value_axis_right_margin_matches_measured_breathing_room() {
        // Regression for the systemic plot-shift bug: axis_y.position="right"
        // (the production default — own-side aligned value labels) previously
        // used the themed axis.labels.padding (6.0) instead of the measured
        // VALUE_AXIS_BREATHING_ROOM (4.0), and AXIS_FREE_EDGE_PAD (the axis-free left
        // edge) was 16.0 instead of 2.0 — together shifting every bar
        // rightward and shrinking the plot. Pin both against directly
        // measured label widths rather than a literal pixel count.
        use serde_json::json;
        let mut payload_json = make_vertical_payload(
            &[("Alpha", 1800.0), ("Beta", 1200.0), ("Gamma", 900.0)],
            false,
        );
        payload_json["resolved"]["style"]["axis_y"]["position"] = json!("right");
        // Production themes bake `ticks.visible: false`; the tick term has
        // its own test below.
        payload_json["resolved"]["style"]["axis_y"]["ticks"]["visible"] = json!(false);
        let payload = make_payload(payload_json.clone());
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        let idx = svg.find("<g transform=\"translate(").expect("plot group present");
        let tag = &svg[idx..idx + 60];
        let plot_left: f64 = tag
            .split("translate(")
            .nth(1)
            .unwrap()
            .split(',')
            .next()
            .unwrap()
            .parse()
            .unwrap();
        // Axis-free left edge under the vega-fit layout: pad_l (16.0 in the
        // fixture) + the zero rule's full-strokeWidth overhang (width 2.0 in
        // the fixture's grid.threshold).
        assert_eq!(plot_left, 16.0 + 2.0);

        // Thousands-grouped (",.0~f" default — see format_y_label's no-ruler
        // branch), not plain digits: "1,000" not "1000".
        let max_val_w = ["0", "500", "1,000", "1,500", "2,000"]
            .iter()
            .map(|s| fonts.measure(s, TEST_FONT, 11.0).expect("measures"))
            .fold(0.0_f64, f64::max);
        // Breathing room hardcoded to 4.0 (not VALUE_AXIS_BREATHING_ROOM);
        // the fit layout ceils the margin the way Vega's autosize does.
        let expected_val_margin = (max_val_w + 4.0).ceil();
        let expected_plot_right = payload.width - 16.0 - expected_val_margin;

        // The rightmost gridline (spans the full plot width, +0.5 crispness
        // translate) pins plot_right.
        let grid_x2s: Vec<f64> = svg
            .match_indices("stroke=\"#D0D3D9\"")
            .filter_map(|(pos, _)| {
                let tag_start = svg[..pos].rfind("<line")?;
                let tag = &svg[tag_start..pos];
                tag.split("x2=\"").nth(1)?.split('"').next()?.parse().ok()
            })
            .collect();
        let plot_w = grid_x2s.into_iter().fold(0.0_f64, f64::max) - 0.5;
        let plot_right = plot_left + plot_w;
        assert!(
            (plot_right - expected_plot_right).abs() < 0.01,
            "plot_right={plot_right} expected={expected_plot_right}"
        );
    }

    /// Vega anchors an axis label at `sign * ((tickSize if ticks visible else
    /// 0) + labelPadding)` (`dbt_charts/core/utils.py:853-860`), so a visible
    /// tick pushes the right axis's label gutter out by `tick length` and the
    /// fit shrinks the plot by the same amount, while the label's far edge
    /// stays at the canvas margin. Production's single chart with
    /// `ticks.visible: true` draws its 3 bars 140 wide against 141.6 with
    /// ticks off (a 6px plot shrink).
    #[test]
    fn vertical_bar_right_value_axis_reserves_the_visible_tick_length() {
        use serde_json::json;
        let render_with = |align: serde_json::Value, visible: bool, length: Option<f64>| {
            let mut payload_json = make_vertical_payload(
                &[("Alpha", 1800.0), ("Beta", 1200.0), ("Gamma", 900.0)],
                false,
            );
            let axis_y = &mut payload_json["resolved"]["style"]["axis_y"];
            axis_y["position"] = json!("right");
            axis_y["labels"]["align"] = align;
            axis_y["title"]["visible"] = json!(true);
            axis_y["ticks"]["visible"] = json!(visible);
            if let Some(len) = length {
                axis_y["ticks"]["length"] = json!(len);
            }
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            let title_x: f64 = svg
                .split("rotate(90 ")
                .nth(1)
                .expect("the right-axis title rotates 90")
                .split(' ')
                .next()
                .unwrap()
                .parse()
                .unwrap();
            let grid_right = horizontal_lines(&svg)
                .into_iter()
                .filter(|(x1, x2, _)| x2 - x1 > 100.0)
                .map(|(_, x2, _)| x2)
                .fold(0.0_f64, f64::max);
            let label_x = text_xs(&svg, ">2,000<")[0];
            (grid_right, label_x, title_x)
        };
        // `"right"` flip-aligns the labels; null is Vega-Lite's default
        // left-aligned (`val_away`) shape, a separate set of anchor sites.
        for align in [json!("right"), json!(null)] {
            let (off_right, off_label, off_title) = render_with(align.clone(), false, None);
            for (length, expected) in [(None, 6.0), (Some(20.0), 20.0)] {
                let (on_right, on_label, on_title) = render_with(align.clone(), true, length);
                assert_eq!(
                    on_title, off_title,
                    "the axis title keeps its x past the labels ({align}, {length:?})"
                );
                assert_eq!(
                    off_right - on_right,
                    expected,
                    "the plot shrinks by the visible tick length ({align}, {length:?})"
                );
                assert_eq!(
                    on_label, off_label,
                    "the label's far edge stays at the canvas margin ({align}, {length:?})"
                );
            }
        }
    }

    #[test]
    fn vertical_bar_measure_axis_bad_position_errors() {
        use serde_json::json;
        let mut payload_json = make_vertical_payload(&[("Alpha", 1000.0)], false);
        payload_json["resolved"]["style"]["axis_y"]["position"] = json!("top");
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let err = render(&payload, &fonts).unwrap_err();
        assert!(err.contains("position"), "error names position: {err}");
    }

    #[test]
    fn vertical_bar_temporal_x_domain_sorted_chronologically() {
        // Rows in reverse-chronological insertion order; production (VL temporal
        // encoding) always sorts the domain ascending regardless of row order.
        use serde_json::json;
        let mut payload_json = make_vertical_payload(
            &[
                ("2024-03-01", 900.0),
                ("2024-01-01", 1800.0),
                ("2024-02-01", 1200.0),
            ],
            false,
        );
        payload_json["resolved"]["x"] = json!("month");
        payload_json["rows"] = json!([
            {"month": "2024-03-01", "revenue": 900.0},
            {"month": "2024-01-01", "revenue": 1800.0},
            {"month": "2024-02-01", "revenue": 1200.0},
        ]);
        payload_json["resolved"]["style"]["mark"]["border"]["radius"] = json!(0.0);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        let jan_pos = svg.find(">Jan<").expect("Jan label present");
        let feb_pos = svg.find(">Feb<").expect("Feb label present");
        let mar_pos = svg.find(">Mar<").expect("Mar label present");
        assert!(
            jan_pos < feb_pos && feb_pos < mar_pos,
            "temporal labels emitted chronologically: Jan({jan_pos}) < Feb({feb_pos}) < Mar({mar_pos})"
        );
    }

    /// Same law as line/area's own `x_is_standard_date`
    /// (`infer_vega_type_from_data`'s STRICT `all_temporal` gate): a
    /// standard `YYYY-MM-DD` x that `detect_time_unit` doesn't bucket
    /// (28-day steps share a weekday but exceed the 14-day median-gap
    /// cutoff, so `detect_time_unit` returns `None`) still headers through
    /// the continuous-date fallback, never the raw ISO string. Regression
    /// coverage for the bug where `bar_datum_aria` hardcoded `false` for
    /// this fact regardless of the actual x shape.
    #[test]
    fn vertical_bar_standard_date_with_no_detected_bucket_formats_a_full_header() {
        let _locale = crate::time_locale::en_us_scope();
        use serde_json::json;
        let mut payload_json = make_vertical_payload(
            &[
                ("2025-01-01", 100.0),
                ("2025-01-29", 200.0),
                ("2025-02-26", 300.0),
            ],
            false,
        );
        payload_json["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        payload_json["resolved"]["style"]["label_usable_ratio"] = json!(0.8);
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("renders");

        assert!(
            svg.contains("aria-label=\"\u{2061}1 Jan 2025; Revenue: 100\""),
            "a standard-date x with no detected bucket headers the tooltip through \
             the continuous fallback, not the raw ISO string: {svg}"
        );
    }

    /// Wiring pin: `render_vertical` must actually pass
    /// `ax_x.fiscal_year_start_month` through to `resolve_temporal_labels`
    /// (bar.rs:1135), not a literal `1` — this test fails if that call site
    /// is ever replaced with a hardcoded calendar-year constant. Twelve
    /// monthly points get a "YYYY" row-2 annotation on the calendar-year
    /// opener (`is_first` always, plus `is_year_start`): under the calendar
    /// default (fiscal_year_start_month=1) only January (the first point)
    /// carries one, so "2024" appears once; under a March fiscal year start,
    /// March also opens a fiscal year and gets its own "2024" annotation,
    /// so "2024" appears twice.
    #[test]
    fn vertical_bar_fiscal_year_start_month_wiring_shifts_year_opener_label() {
        use serde_json::json;
        let months: Vec<(String, f64)> = (1..=12)
            .map(|m| (format!("2024-{m:02}-01"), 100.0 + m as f64))
            .collect();
        let cat_refs: Vec<(&str, f64)> = months.iter().map(|(d, v)| (d.as_str(), *v)).collect();

        let render_with_fiscal_start = |fiscal_start: u8| -> String {
            let mut payload_json = make_vertical_payload(&cat_refs, false);
            payload_json["resolved"]["x"] = json!("month");
            payload_json["rows"] =
                json!(months.iter().map(|(d, v)| json!({"month": d, "revenue": v})).collect::<Vec<_>>());
            payload_json["resolved"]["style"]["axis_x"]["fiscal_year_start_month"] = json!(fiscal_start);
            payload_json["width"] = json!(1200.0);
            let payload = make_payload(payload_json);
            let fonts = test_fonts();
            render(&payload, &fonts).expect("renders")
        };

        let calendar_svg = render_with_fiscal_start(1);
        let fiscal_svg = render_with_fiscal_start(3);

        let count_year_labels = |svg: &str| svg.matches(">2024<").count();
        assert_eq!(count_year_labels(&calendar_svg), 1, "calendar year: only January opens 2024: {calendar_svg}");
        assert_eq!(
            count_year_labels(&fiscal_svg),
            2,
            "fiscal year starting March: January (first) and March (fiscal open) both carry 2024: {fiscal_svg}"
        );
    }

    /// Production keys every row by the bucket that holds it
    /// (`complete_ordinal_time_series`), so yearly January dates under an April
    /// fiscal start draw one bar per fiscal-year band (2022-04, 2023-04) rather
    /// than matching no band.
    #[test]
    fn vertical_bar_rows_off_a_fiscal_year_start_land_in_their_enclosing_bands() {
        use serde_json::json;
        let render_dates = |dates: [&str; 2], fiscal_start: u8| -> String {
            let cats = [(dates[0], 100.0), (dates[1], 130.0)];
            let mut payload_json = make_vertical_payload(&cats, false);
            payload_json["resolved"]["x"] = json!("month");
            payload_json["rows"] = json!(cats.iter().map(|(d, v)| json!({"month": d, "revenue": v})).collect::<Vec<_>>());
            payload_json["resolved"]["style"]["axis_x"]["fiscal_year_start_month"] = json!(fiscal_start);
            render(&make_payload(payload_json), &test_fonts()).expect("renders")
        };
        let bars = |svg: &str| svg.matches("Z\" fill=\"#3164a3\"/>").count();
        let off_anchor = render_dates(["2023-01-01", "2024-01-01"], 4);
        assert_eq!(bars(&off_anchor), 2, "{off_anchor}");
        assert_eq!(bars(&render_dates(["2023-04-01", "2024-04-01"], 4)), 2);
        // Month-ends belong to their month's band the same way.
        let month_ends = render_dates(["2024-01-31", "2024-02-29"], 1);
        assert_eq!(bars(&month_ends), 2, "{month_ends}");
    }

    /// …and it is the only family the title contributes — the measured one
    /// as well as the painted one.
    ///
    /// Production's `resolve_title_font` returns the BODY family at the tiny
    /// and narrow tiers, so on the default theme `_NARROW_MAX` switches the
    /// title from serif to sans, and the compiled spec carries that resolved
    /// family for Vega to measure in. The fit arms measure the title to feed
    /// `fit_plot`'s title box, so the un-tiered slot must not reach that
    /// either: two families 57px apart on this string, and the render is
    /// byte-identical.
    ///
    /// Fail-first at this card width, and the width is the whole point. The
    /// title box is capped at the padded view width, and
    /// `vega_fit::tests::a_capped_title_reaches_the_right_margin_only_at_its_cap`
    /// shows the width is inert BELOW that cap — which looks at first like
    /// the wrong-family read being harmless. It is the opposite: the wrong
    /// family is by construction the WIDER one, so on a narrow card it is
    /// the one that reaches the cap while the tiered family stays under it.
    /// At 350px the pre-fix read yields a plot 7px narrower (`x2` 272.5 vs
    /// 279.5), moving every bar with it. Above ~370px neither family clamps
    /// and the two agree, so a width picked there would pin nothing.
    #[test]
    fn tiered_title_font_family_measures_the_band_too() {
        use serde_json::json;
        // Far enough apart to be unmistakable: at 11px this title measures
        // 285.6 in Inter and 343.2 in Source Code Pro.
        const WIDE_FONT: &str = "'Source Code Pro', monospace";
        let long = "Revenue by product across every region we operate in";
        let build = |untiered_family: &str| {
            let mut p = make_vertical_payload(&[("Alpha", 10.0), ("Beta", 20.0)], false);
            p["resolved"]["title"] = json!(long);
            p["resolved"]["title_style"]["font"]["family"] = json!(untiered_family);
            // Pin the un-tiered SIZE to the tiered one: this test isolates
            // FAMILY passthrough, and the pre-pass wrap now measures at
            // `title_style.font.size` (production's own precedence) — an
            // unset size here would fall to 18 and wrap this title on its
            // own, unrelated to the family question this test asks.
            p["resolved"]["title_style"]["font"]["size"] = json!(11.0);
            p["resolved"]["style"]["title_font"] = json!({
                "family": TEST_FONT, "size": 11.0, "weight": "600", "color": "#222222"
            });
            // A narrow card: the tier this change exists to serve, and the
            // only width where the title comes near its cap at all.
            p["width"] = json!(350.0);
            make_payload(p)
        };
        let fonts = test_fonts();
        let wide = render(&build(WIDE_FONT), &fonts).expect("renders");
        let sans = render(&build(TEST_FONT), &fonts).expect("renders");

        let pos = wide.find(&format!(">{long}<")).expect("title present");
        let tag = &wide[wide[..pos].rfind("<text").unwrap()..pos];
        assert!(tag.contains(TEST_FONT), "the tiered family paints: {tag}");

        assert_eq!(
            wide, sans,
            "the un-tiered `title_style.font.family` must not reach anything \
             once `style.title_font` carries the answer"
        );
    }

    /// The width-resolved `style.title_font` beats the un-tiered
    /// `title_style.font`, on bar as it already does on
    /// line/pie/scatter/table. Production sizes an OBJECT title by its own
    /// card's width tier (`chart_title_spec`) and hands the answer over in
    /// this slot: a 342px card titles at 11px/600 Inter, not the 18px serif
    /// the un-tiered slot carries. Reading the wrong one is not only wrong
    /// ink — the title band it reserves moves the whole plot.
    #[test]
    fn width_resolved_title_font_beats_title_style_font() {
        use serde_json::json;
        let mut payload_json =
            make_vertical_payload(&[("Alpha", 10.0), ("Beta", 20.0)], false);
        payload_json["resolved"]["style"]["title_font"] = json!({
            "family": TEST_FONT, "size": 11.0, "weight": "600", "color": "#222222"
        });
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("renders");

        let pos = svg.find(">Test Vertical Bar<").expect("title present");
        let tag = &svg[svg[..pos].rfind("<text").unwrap()..pos];
        assert!(tag.contains("font-size=\"11\""), "tiered 11px wins: {tag}");
        assert!(tag.contains("font-weight=\"600\""), "tiered weight wins: {tag}");

        // …and the band it reserves shrinks with it: the plot sits higher
        // than it does under the un-tiered 18px slot.
        let plot_top = |s: &str| -> f64 {
            let i = s.find("<g transform=\"translate(").expect("plot group");
            s[i..].split(',').nth(1).unwrap().split(')').next().unwrap().trim().parse().unwrap()
        };
        let untiered = render(
            &make_payload(make_vertical_payload(&[("Alpha", 10.0), ("Beta", 20.0)], false)),
            &test_fonts(),
        )
        .expect("renders");
        assert!(
            plot_top(&svg) < plot_top(&untiered),
            "the 11px title reserves a smaller band ({}) than the 18px one ({})",
            plot_top(&svg),
            plot_top(&untiered)
        );
    }

    /// The two lines production's `title_overflow.py` wraps
    /// `WRAPPING_SUBTITLE` onto at the 344px padded-card limit — the exact
    /// pair `xaxis-cadence-ladder/third_b`'s compiled spec carries
    /// (`title.subtitle` is a two-element array there).
    const WRAPPING_SUBTITLE_LINE_1: &str =
        "Expect: skip first, then tilt; bar data and monthly ticks are";
    const WRAPPING_SUBTITLE_LINE_2: &str = "unchanged.";

    /// A subtitle wider than the padded card wraps to two measured lines
    /// (`apply_title_overflow_to_spec`, wrap-two) and Vega's title group
    /// grows by `sub_fs + 2` — the text mark's default lineHeight — per
    /// extra line, on the fit arm rather than dropping to the legacy
    /// margins.
    ///
    /// Probed on production's own compiled `third_b` spec: scenegraph
    /// origin y 67 with the two wrapped lines, 54 forced to one, 40 with no
    /// subtitle at all. This arm drew 40 — a flat 27px shift of everything
    /// below the title.
    #[test]
    fn an_overflowing_subtitle_wraps_to_two_measured_lines() {
        use serde_json::json;
        let long = format!("{WRAPPING_SUBTITLE_LINE_1} {WRAPPING_SUBTITLE_LINE_2}");
        let build = |subtitle: &str| {
            let mut p = make_vertical_payload(&[("Alpha", 10.0), ("Beta", 20.0)], false);
            // third_b's own card: 376px wide at 16px padding, so the wrap
            // limit is `int(376 − 16 − 16)` = 344.
            p["width"] = json!(376.0);
            p["resolved"]["title"] = json!("Third Width — Bar");
            p["resolved"]["title_style"] = json!({
                "font": {"family": TEST_FONT, "size": 14.0, "weight": 500.0, "case": "none"},
                "position": {"offset": 10.0},
                "overflow": "truncate",
                "subtitle": {
                    "font": {"family": TEST_FONT, "size": 11.0, "weight": "400"},
                    "overflow": "wrap-two"
                }
            });
            p["resolved"]["subtitle"] = json!(subtitle);
            make_payload(p)
        };
        let fonts = test_fonts();
        // Discriminating premise: the whole subtitle outruns the 344px
        // padded card at its 11px slot, so production wraps it.
        assert!(fonts.measure(&long, "Inter", 11.0).unwrap() > 344.0);
        assert!(fonts.measure(WRAPPING_SUBTITLE_LINE_1, "Inter", 11.0).unwrap() <= 344.0);

        let wrapped = render(&build(&long), &fonts).expect("renders");
        let one_line = render(&build("Monthly ticks."), &fonts).expect("renders");

        assert_two_subtitle_rows(&wrapped, WRAPPING_SUBTITLE_LINE_1, WRAPPING_SUBTITLE_LINE_2);
        assert_eq!(
            plot_group_top(&wrapped) - plot_group_top(&one_line),
            13.0,
            "the second line deepens the fit top margin: {wrapped}"
        );
    }

    /// The `<text …>` tag whose content is `needle`.
    fn text_tag(svg: &str, needle: &str) -> String {
        let pos = svg
            .find(&format!(">{needle}<"))
            .unwrap_or_else(|| panic!("{needle} missing: {svg}"));
        svg[svg[..pos].rfind("<text").unwrap()..pos].to_string()
    }

    fn tag_attr(tag: &str, name: &str) -> f64 {
        tag.split(&format!(" {name}=\"")).nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
    }

    /// The plot group's y translate — every fit arm's negotiated `plot_top`.
    fn plot_group_top(svg: &str) -> f64 {
        let i = svg.find("<g transform=\"translate(").expect("plot group");
        svg[i..].split(',').nth(1).unwrap().split(')').next().unwrap().trim().parse().unwrap()
    }

    /// Both wrapped subtitle rows are painted, sharing the title anchor and
    /// one `sub_fs + 2` lineHeight apart.
    fn assert_two_subtitle_rows(svg: &str, line1: &str, line2: &str) {
        let first = text_tag(svg, line1);
        let second = text_tag(svg, line2);
        assert_eq!(
            tag_attr(&second, "x"),
            tag_attr(&first, "x"),
            "lines share the title anchor"
        );
        assert_eq!(
            tag_attr(&second, "y") - tag_attr(&first, "y"),
            11.0 + 2.0,
            "the fixtures' 11px subtitle advances by sub_fs + 2"
        );
    }

    /// The two lines production wraps `CARD_WIDE_SUBTITLE` onto at the 568px
    /// padded-card limit of a 600px card — the basis both the horizontal fit
    /// arm and the quantitative-x arm carry.
    const CARD_WIDE_SUBTITLE_LINE_1: &str =
        "Quarterly revenue by product line, measured against the approved plan \
         and the prior year's own quarterly";
    const CARD_WIDE_SUBTITLE_LINE_2: &str = "run rate.";

    /// The title style every production payload resolves: an authored
    /// subtitle font, so the block measures its own lines.
    fn subtitled_title_style(title_size: f64) -> serde_json::Value {
        serde_json::json!({
            "font": {"family": TEST_FONT, "size": title_size, "weight": 500.0, "case": "none"},
            "position": {"offset": 10.0},
            "subtitle": {"font": {"family": TEST_FONT, "size": 11.0, "weight": "400"}}
        })
    }

    /// The HORIZONTAL fit arm wraps at the padded card, like the vertical
    /// one: production compiles one title BLOCK for both orientations
    /// (`resolve/chart/bar.py`) and runs `apply_title_overflow_to_spec` over
    /// the unit spec whatever the orientation. Before the lift this arm
    /// measured the subtitle only to decide whether to reserve one line, and
    /// painted the overflowing string unwrapped.
    #[test]
    fn horizontal_arm_wraps_a_card_wide_subtitle_onto_two_lines() {
        use serde_json::json;
        let long = format!("{CARD_WIDE_SUBTITLE_LINE_1} {CARD_WIDE_SUBTITLE_LINE_2}");
        let build = |subtitle: &str| {
            let mut p = make_horizontal_payload();
            p["resolved"]["title"] = json!("Revenue by product");
            p["resolved"]["title_style"] = subtitled_title_style(18.0);
            p["resolved"]["subtitle"] = json!(subtitle);
            make_payload(p)
        };
        let fonts = test_fonts();
        // Discriminating premise: the whole subtitle outruns the 600px
        // card's 568px limit at its 11px slot; its first line fits.
        assert!(fonts.measure(&long, "Inter", 11.0).unwrap() > 568.0);
        assert!(fonts.measure(CARD_WIDE_SUBTITLE_LINE_1, "Inter", 11.0).unwrap() <= 568.0);

        let wrapped = render(&build(&long), &fonts).expect("renders");
        let one_line = render(&build("Q3 FY25"), &fonts).expect("renders");

        assert_two_subtitle_rows(&wrapped, CARD_WIDE_SUBTITLE_LINE_1, CARD_WIDE_SUBTITLE_LINE_2);
        assert_eq!(
            plot_group_top(&wrapped) - plot_group_top(&one_line),
            13.0,
            "the second line deepens the horizontal fit top margin: {wrapped}"
        );
    }

    /// The QUANTITATIVE-x arm carries the same title block. It drew no
    /// subtitle at all before the lift and bailed a card-wide title to the
    /// band legacy; production compiles a plain unit spec here too, so the
    /// subtitle paints, reserves its band, and wraps at the padded card.
    #[test]
    fn continuous_x_arm_draws_reserves_and_wraps_the_subtitle() {
        use serde_json::json;
        let long = format!("{CARD_WIDE_SUBTITLE_LINE_1} {CARD_WIDE_SUBTITLE_LINE_2}");
        let xs: Vec<(f64, f64)> = (0..24).map(|h| (h as f64, 100.0 + h as f64)).collect();
        let build = |subtitle: Option<&str>| {
            let mut p = make_continuous_payload(&xs);
            p["resolved"]["title_style"] = subtitled_title_style(18.0);
            if let Some(text) = subtitle {
                p["resolved"]["subtitle"] = json!(text);
            }
            make_payload(p)
        };
        let fonts = test_fonts();
        assert!(fonts.measure(&long, "Inter", 11.0).unwrap() > 568.0);

        let none = render(&build(None), &fonts).expect("renders");
        let one_line = render(&build(Some("Hourly volume")), &fonts).expect("renders");
        let wrapped = render(&build(Some(&long)), &fonts).expect("renders");

        assert!(one_line.contains(">Hourly volume<"), "the subtitle paints: {one_line}");
        assert_eq!(
            plot_group_top(&one_line) - plot_group_top(&none),
            11.0 + crate::constants::layout::VEGA_SUBTITLE_PADDING,
            "…and reserves its own band: {one_line}"
        );
        assert_two_subtitle_rows(&wrapped, CARD_WIDE_SUBTITLE_LINE_1, CARD_WIDE_SUBTITLE_LINE_2);
        assert_eq!(
            plot_group_top(&wrapped) - plot_group_top(&one_line),
            13.0,
            "the second line deepens the quantitative-x fit top margin: {wrapped}"
        );
    }

    /// An empty title is no title: production's title block is absent
    /// entirely when the author wrote none, so nothing paints and nothing
    /// is reserved. Measured on `fixtures/baseline-bar`'s revenue_chart
    /// (`title: ""`, `subtitle: ""`): production's plot origin is (42, 18)
    /// — pad_t alone — where reserving a phantom band puts it at (42, 46),
    /// exactly one 18px title plus its 10px offset lower, under an empty
    /// `<text>` element.
    #[test]
    fn empty_title_reserves_no_band_and_paints_nothing() {
        use serde_json::json;
        let mut payload_json =
            make_vertical_payload(&[("Alpha", 10.0), ("Beta", 20.0)], false);
        payload_json["resolved"]["title"] = json!("");
        payload_json["resolved"]["subtitle"] = json!("");
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        assert!(
            !svg.contains("></text>"),
            "no empty text element is painted: {svg}"
        );

        // Same chart with a real title, to measure the band it reserves.
        let mut titled_json =
            make_vertical_payload(&[("Alpha", 10.0), ("Beta", 20.0)], false);
        titled_json["resolved"]["subtitle"] = json!("");
        let titled = render(&make_payload(titled_json), &fonts).expect("renders");

        let plot_top = |s: &str| -> f64 {
            let i = s.find("<g transform=\"translate(").expect("plot group");
            s[i..].split(',').nth(1).unwrap().split(')').next().unwrap().trim().parse().unwrap()
        };
        let (empty_top, titled_top) = (plot_top(&svg), plot_top(&titled));
        assert!(
            titled_top - empty_top >= 27.0,
            "a real title reserves its band ({titled_top}) where an empty one \
             reserves none ({empty_top})"
        );
    }

    /// …but a WHITESPACE title is a real title under the mode every shipped
    /// theme authors. Production gates on Python truthiness
    /// (`set_chart_title`), and `"   "` is truthy, so the block survives
    /// into the spec; `prepare_title_text` then decides what reaches Vega —
    /// `truncate` (and `clip`) return the raw string when the wrap yields no
    /// lines, so the band survives, while `wrap-two` joins the empty list
    /// into `""` and Vega reserves nothing (probed: title group origin y 49
    /// for `"   "` against 21 for `""`). Trimming unconditionally would
    /// shift a truncate-themed chart 28px off the reference.
    #[test]
    fn whitespace_title_reserves_its_band_only_where_the_mode_keeps_it() {
        use serde_json::json;
        let plot_top = |s: &str| -> f64 {
            let i = s.find("<g transform=\"translate(").expect("plot group");
            s[i..].split(',').nth(1).unwrap().split(')').next().unwrap().trim().parse().unwrap()
        };
        let fonts = test_fonts();
        let spaces = |overflow: &str| {
            let mut j = make_vertical_payload(&[("Alpha", 10.0), ("Beta", 20.0)], false);
            j["resolved"]["title"] = json!("   ");
            j["resolved"]["title_style"]["overflow"] = json!(overflow);
            render(&make_payload(j), &fonts).expect("renders")
        };
        let titled = render(
            &make_payload(make_vertical_payload(&[("Alpha", 10.0), ("Beta", 20.0)], false)),
            &fonts,
        )
        .expect("renders");
        let untitled = {
            let mut j = make_vertical_payload(&[("Alpha", 10.0), ("Beta", 20.0)], false);
            j["resolved"]["title"] = json!("");
            render(&make_payload(j), &fonts).expect("renders")
        };
        assert_eq!(
            plot_top(&spaces("truncate")),
            plot_top(&titled),
            "under the themes' truncate a whitespace title reserves a real title's band"
        );
        assert_eq!(
            plot_top(&spaces("wrap-two")),
            plot_top(&untitled),
            "under wrap-two it collapses to no title at all"
        );
    }

    /// Regression for the temporal-label overhang bug: a narrow-band
    /// "Jan"/"2025" opener label wider than its own half-band must grow the
    /// axis-free left margin (val axis on the right), not sit at the fixed
    /// `card_padding + AXIS_FREE_EDGE_PAD` baseline. Numbers mirror production's
    /// `playground/temporal-label-cadence-transitions` `wk_52` case: 52
    /// weekly Mondays from 2025-01-06 on a 564×202 canvas (card_padding
    /// 16.0) — measured production plot_left is 26.0, vs the 18.0 baseline
    /// this bug used to leave it at.
    #[test]
    fn vertical_bar_temporal_overhang_grows_axis_free_left_margin() {
        use serde_json::json;
        let cats: Vec<(String, f64)> = (0..52)
            .map(|i| {
                let days = i * 7;
                let date = chrono_like_date(2025, 1, 6, days);
                (date, 120.0 + ((i * 37) % 45) as f64)
            })
            .collect();
        let cat_refs: Vec<(&str, f64)> = cats.iter().map(|(d, v)| (d.as_str(), *v)).collect();
        let mut payload_json = make_vertical_payload(&cat_refs, false);
        payload_json["resolved"]["x"] = json!("week");
        payload_json["rows"] = json!(cats
            .iter()
            .map(|(d, v)| json!({"week": d, "revenue": v}))
            .collect::<Vec<_>>());
        payload_json["resolved"]["style"]["axis_y"]["position"] = json!("right");
        payload_json["resolved"]["style"]["axis_y"]["tick_values"] =
            json!([0.0, 50.0, 100.0, 150.0]);
        payload_json["width"] = json!(564.0);
        payload_json["height"] = json!(202.0);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        let idx = svg.find("<g transform=\"translate(").expect("plot group present");
        let tag = &svg[idx..idx + 60];
        let plot_left: f64 = tag
            .split("translate(")
            .nth(1)
            .unwrap()
            .split(',')
            .next()
            .unwrap()
            .parse()
            .unwrap();

        // Baseline (no overhang) would be exactly 16.0 + 2.0 = 18.0; the
        // measured production margin is 26.0, and the fit layout now lands
        // it EXACTLY: the first cadence label's ink carries the ±0.5
        // text-bounds expansion (vega-scenegraph's bound() expand under
        // vega's rounded layout — see the ink closure), which is the term
        // that closes the old 1px gap. This equality is the pin for that
        // model: "simplifying" the ±0.5 away fails here.
        assert!(
            (plot_left - 26.0).abs() < 0.01,
            "plot_left {plot_left} should equal the measured production margin 26.0"
        );
    }

    /// A thinned temporal band axis restores tick marks: production's
    /// The `(y1, y2)` of every vertical rule in `svg`: a bar chart's x tick marks. Ticks wear the baseline's stroke where one
    /// sits on the floor, so they are found by shape.
    fn vertical_line_spans(svg: &str) -> Vec<(f64, f64)> {
        let attr = |tag: &str, name: &str| -> f64 { tag.split(&format!("{name}=\"")).nth(1).unwrap().split('"').next().unwrap().parse().unwrap() };
        svg.split("<line ")
            .skip(1)
            .map(|el| &el[..el.find('>').unwrap()])
            .filter(|tag| attr(tag, "x1") == attr(tag, "x2"))
            .map(|tag| (attr(tag, "y1"), attr(tag, "y2")))
            .collect()
    }

    /// Once calendar labels grow sparser than buckets, ticks are restored
    /// over the theme's hidden default, one under each painted label (not
    /// one per band), and the label band sits a tick-length lower.
    #[test]
    fn thinned_temporal_band_axis_restores_ticks_below_the_plot() {
        use serde_json::json;
        let cats: Vec<(String, f64)> = (0..52)
            .map(|i| {
                let date = chrono_like_date(2025, 1, 6, i * 7);
                (date, 120.0 + ((i * 37) % 45) as f64)
            })
            .collect();
        let cat_refs: Vec<(&str, f64)> = cats.iter().map(|(d, v)| (d.as_str(), *v)).collect();
        let mut payload_json = make_vertical_payload(&cat_refs, false);
        payload_json["resolved"]["x"] = json!("week");
        payload_json["rows"] = json!(cats
            .iter()
            .map(|(d, v)| json!({"week": d, "revenue": v}))
            .collect::<Vec<_>>());
        payload_json["resolved"]["style"]["axis_y"]["position"] = json!("right");
        payload_json["resolved"]["style"]["axis_y"]["tick_values"] =
            json!([0.0, 50.0, 100.0, 150.0]);
        // The theme hides bar x ticks; the restoration must override it.
        payload_json["resolved"]["style"]["axis_x"]["ticks"] = json!({
            "visible": false, "color": "#EDEFF2", "width": 1.0,
            "length": 6.0, "offset": 1.0, "count": null
        });
        payload_json["width"] = json!(564.0);
        payload_json["height"] = json!(202.0);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        let tick_count = vertical_line_spans(&svg).len();
        assert_eq!(tick_count, 12, "one restored tick per month label, none under the other of 52 weeks: {svg}");
    }

    /// 26 weekly Mondays — thins to month openers at these widths, firing
    /// the tick restoration on whichever arm renders the chart.
    fn weekly_cats() -> Vec<(String, f64)> {
        (0..26)
            .map(|i| (chrono_like_date(2025, 1, 6, i * 7), 120.0 + ((i * 37) % 45) as f64))
            .collect()
    }

    /// Regression (review round 2): the restored tick length must be
    /// BUDGETED on every arm that emits it. The legacy arm (right legend)
    /// reserved no tick length, so its ticks pushed the two-row label band
    /// 6px into the bottom padding.
    #[test]
    fn legacy_arm_budgets_the_restored_tick_length() {
        use serde_json::json;
        let cats = weekly_cats();
        let cat_refs: Vec<(&str, f64)> = cats.iter().map(|(d, v)| (d.as_str(), *v)).collect();
        let mut payload_json = make_vertical_payload(&cat_refs, true);
        payload_json["resolved"]["x"] = json!("week");
        payload_json["rows"] = json!(cats
            .iter()
            .flat_map(|(d, v)| vec![
                json!({"week": d, "region": "North", "revenue": v}),
                json!({"week": d, "region": "South", "revenue": v * 0.6}),
            ])
            .collect::<Vec<_>>());
        payload_json["resolved"]["color"] = json!("region");
        payload_json["resolved"]["legend"]["visible"] = json!(true);
        payload_json["resolved"]["legend"]["position"] = json!({"edge": "right", "align": "start", "overlay": false});
        payload_json["width"] = json!(564.0);
        payload_json["height"] = json!(202.0);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("legacy grouped weekly renders");

        // Restored ticks drawn (one per label), starting at the plot bottom.
        let tick_spans = vertical_line_spans(&svg);
        assert_eq!(tick_spans.len(), 6, "one restored tick per month label of 26 weeks\n{svg}");
        let (ty1, ty2) = tick_spans[0];
        assert!((ty2 - ty1 - 6.5).abs() < 0.01, "a tick runs from the plot bottom through the crisp 0.5 and length 6: {ty1}..{ty2}");
        // The two-row label band below the ticks stays inside the bottom
        // padding: with the tick unbudgeted the band overflows h − pad_b by
        // the full tick length (fixture pad 16, label pad 6, two rows
        // fs*2+2 = 24; the crisp +0.5 the tick paints at is the only
        // tolerated overshoot).
        assert!(
            ty2 + 6.0 + 24.0 <= 202.0 - 16.0 + 0.51,
            "tick bottom {ty2} + label band 30 must fit above h − pad_b = 186\n{svg}"
        );
        // And the labels themselves clear the ticks: the legacy arm's own
        // `cat_label_y` carries the same tick length, so row 1's baseline
        // sits a full ascent below the tick bottom.
        let row1_y: f64 = {
            let pos = svg.find(">Jan<").expect("month-opener label present");
            let tag = &svg[svg[..pos].rfind("<text").unwrap()..pos];
            tag.split("y=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        assert!(
            row1_y >= ty2 + 6.0,
            "row-1 baseline {row1_y} clears tick bottom {ty2} by the label padding\n{svg}"
        );
    }

    /// Same regression on the composed support-table arm: its canvas grows
    /// by exactly the restored tick length, keeping the label band inside
    /// the bottom padding.
    #[test]
    fn composed_arm_budgets_the_restored_tick_length() {
        use serde_json::json;
        let cats = weekly_cats();
        let cat_refs: Vec<(&str, f64)> = cats.iter().map(|(d, v)| (d.as_str(), *v)).collect();
        let mut payload_json = make_vertical_payload(&cat_refs, false);
        payload_json["resolved"]["x"] = json!("week");
        payload_json["rows"] = json!(cats
            .iter()
            .map(|(d, v)| json!({"week": d, "revenue": v}))
            .collect::<Vec<_>>());
        payload_json["resolved"]["style"]["axis_y"]["position"] = json!("right");
        payload_json["resolved"]["support_table"] = json!({
            "entries": [{"source": "revenue", "format": ",d", "label": "Units"}]
        });
        payload_json["resolved"]["effective_support_table_style"] = support_table_style_json();
        payload_json["width"] = json!(564.0);
        payload_json["height"] = json!(202.0);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("composed weekly strip renders");

        // Pin the routing: only the composed arm grows the canvas past the
        // slot width (the legacy splice keeps the slot), so this asserts the
        // test reaches the arm it names.
        let canvas_w: f64 =
            svg.split("width=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
        assert!(canvas_w > 564.0, "composed arm grows the canvas past the slot: {canvas_w}");
        let canvas_h: f64 = svg
            .split("height=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
        let ty2 = vertical_line_spans(&svg).first().expect("restored ticks drawn").1;
        // Below the tick bottom the canvas still holds label pad 6 + two
        // label rows 24 + pad_b 16; without the budgeted tick length it
        // comes out 6 short.
        assert!(
            canvas_h >= ty2 + 6.0 + 24.0 + 16.0 - 1.0,
            "canvas {canvas_h} budgets pad+rows+padding below tick bottom {ty2}\n{svg}"
        );
    }

    /// The composed support-table arm reserves the right value axis's tick
    /// length like every other right axis (`sign * (tickSize +
    /// labelPadding)`, `utils.py:853-860`): toggling `axis_y.ticks` grows the
    /// canvas by exactly the tick length.
    #[test]
    fn composed_arm_right_axis_reserves_the_visible_tick_length() {
        use serde_json::json;
        let canvas_width = |visible: bool, length: Option<f64>| -> f64 {
            let cats = weekly_cats();
            let cat_refs: Vec<(&str, f64)> = cats.iter().map(|(d, v)| (d.as_str(), *v)).collect();
            let mut payload_json = make_vertical_payload(&cat_refs, false);
            payload_json["resolved"]["x"] = json!("week");
            payload_json["rows"] = json!(cats
                .iter()
                .map(|(d, v)| json!({"week": d, "revenue": v}))
                .collect::<Vec<_>>());
            let axis_y = &mut payload_json["resolved"]["style"]["axis_y"];
            axis_y["position"] = json!("right");
            axis_y["ticks"]["visible"] = json!(visible);
            if let Some(len) = length {
                axis_y["ticks"]["length"] = json!(len);
            }
            payload_json["resolved"]["support_table"] = json!({
                "entries": [{"source": "revenue", "format": ",d", "label": "Units"}]
            });
            payload_json["resolved"]["effective_support_table_style"] = support_table_style_json();
            payload_json["width"] = json!(564.0);
            payload_json["height"] = json!(202.0);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("composed strip renders");
            svg.split("width=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        let off = canvas_width(false, None);
        assert!(off > 564.0, "this payload reaches the composed arm: {off}");
        for (length, expected) in [(None, 6.0), (Some(20.0), 20.0)] {
            assert_eq!(canvas_width(true, length) - off, expected, "{length:?}");
        }
    }

    /// `(x1, x2, y)` of every horizontal `<line>`.
    fn horizontal_lines(svg: &str) -> Vec<(f64, f64, f64)> {
        svg.split("<line ")
            .skip(1)
            .filter_map(|tag| {
                let attr = |name: &str| -> Option<f64> {
                    tag.split(&format!("{name}=\"")).nth(1)?.split('"').next()?.parse().ok()
                };
                let (y1, y2) = (attr("y1")?, attr("y2")?);
                (y1 == y2).then_some((attr("x1")?, attr("x2")?, y1))
            })
            .collect()
    }

    /// The value-axis tick marks: horizontal lines exactly `len` long (a
    /// gridline spans the plot, never the tick).
    fn tick_marks(svg: &str, len: f64) -> Vec<(f64, f64, f64)> {
        horizontal_lines(svg)
            .into_iter()
            .filter(|(x1, x2, _)| (x2 - x1 - len).abs() < 1e-6)
            .collect()
    }

    /// The x of every right-aligned numeric `<text>`: the value-axis labels.
    fn value_label_xs(svg: &str) -> Vec<f64> {
        svg.split("<text ")
            .skip(1)
            .filter_map(|tag| {
                let body = tag.split("</text>").next()?;
                let content = body.rsplit('>').next()?;
                let numeric =
                    !content.is_empty() && content.chars().all(|c| "0123456789,.".contains(c));
                (numeric && body.contains("text-anchor=\"end\"")).then(|| {
                    body.split("x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
                })
            })
            .collect()
    }

    /// Vega draws a tick per rung on a left value axis too, ending at the
    /// axis edge, and anchors its labels at `-(tickSize + labelPadding)`
    /// (`utils.py:853-860`): the plot gives up the tick length on the left
    /// while the labels keep their absolute x. `render_with(visible, length)`
    /// renders the arm under test; `rungs` is its ladder's size.
    fn assert_left_axis_paints_and_reserves_the_visible_tick(
        render_with: impl Fn(bool, Option<f64>) -> String,
        rungs: usize,
    ) {
        let off = render_with(false, None);
        let (off_left, off_labels) = (plot_group_left(&off), value_label_xs(&off));
        assert!(!off_labels.is_empty(), "the arm paints value labels: {off}");
        for (length, expected) in [(None, 6.0), (Some(14.0), 14.0)] {
            let on = render_with(true, length);
            assert!(tick_marks(&off, expected).is_empty(), "ticks off paints no mark ({length:?})");
            let ticks = tick_marks(&on, expected);
            assert_eq!(ticks.len(), rungs, "one mark per rung ({length:?}): {on}");
            let plot_left = plot_group_left(&on);
            assert_eq!(plot_left - off_left, expected, "the plot gives up the tick ({length:?})");
            for (_, x2, _) in &ticks {
                assert_eq!(*x2, plot_left + 0.5, "the mark ends at the axis edge ({length:?})");
            }
            assert_eq!(value_label_xs(&on), off_labels, "labels keep absolute x ({length:?})");
        }
    }

    #[test]
    fn fit_arm_left_value_axis_paints_and_reserves_the_visible_tick() {
        use serde_json::json;
        let render_with = |visible: bool, length: Option<f64>| {
            let mut payload_json = make_vertical_payload(
                &[("Alpha", 1800.0), ("Beta", 1200.0), ("Gamma", 900.0)],
                false,
            );
            let axis_y = &mut payload_json["resolved"]["style"]["axis_y"];
            axis_y["ticks"]["visible"] = json!(visible);
            if let Some(len) = length {
                axis_y["ticks"]["length"] = json!(len);
            }
            render(&make_payload(payload_json), &test_fonts()).expect("renders")
        };
        assert_left_axis_paints_and_reserves_the_visible_tick(render_with, 5);

        // Each mark sits on its rung's rounded gridline row, offset by the
        // plot top.
        let on = render_with(true, None);
        let top = plot_group_top(&on);
        let grid_ys: Vec<f64> = horizontal_lines(&on)
            .into_iter()
            .filter(|(x1, x2, _)| *x1 == 0.5 && x2 - x1 > 100.0)
            .map(|(_, _, y)| y)
            .collect();
        let tick_ys: Vec<f64> = tick_marks(&on, 6.0).into_iter().map(|(_, _, y)| y - top).collect();
        // The tick at 0 continues the zero rule, at the rule's exact row instead of the gridline's snapped one.
        let zero_row = grid_ys.iter().cloned().fold(f64::MIN, f64::max);
        let expected: Vec<f64> = grid_ys.iter().map(|&y| if y == zero_row { y - 0.5 } else { y }).collect();
        assert_eq!(tick_ys, expected);
    }

    #[test]
    fn fit_arm_right_value_axis_paints_the_visible_tick() {
        use serde_json::json;
        let mut payload_json = make_vertical_payload(
            &[("Alpha", 1800.0), ("Beta", 1200.0), ("Gamma", 900.0)],
            false,
        );
        let axis_y = &mut payload_json["resolved"]["style"]["axis_y"];
        axis_y["position"] = json!("right");
        axis_y["ticks"] = json!({"color": "#D0D3D9", "visible": true, "count": 6.0, "length": 9.0});
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        let plot_w = horizontal_lines(&svg).first().map(|(x1, x2, _)| x2 - x1).expect("a gridline");
        let ticks = tick_marks(&svg, 9.0);
        assert_eq!(ticks.len(), 5, "one mark per rung: {svg}");
        for (x1, _, _) in &ticks {
            let edge = plot_group_left(&svg) + plot_w + 0.5;
            assert_eq!(*x1, edge, "the mark starts at the axis edge");
        }
    }

    #[test]
    fn composed_arm_left_value_axis_paints_and_reserves_the_visible_tick() {
        use serde_json::json;
        let render_with = |visible: bool, length: Option<f64>| {
            let cats = weekly_cats();
            let cat_refs: Vec<(&str, f64)> = cats.iter().map(|(d, v)| (d.as_str(), *v)).collect();
            let mut payload_json = make_vertical_payload(&cat_refs, false);
            payload_json["resolved"]["x"] = json!("week");
            payload_json["rows"] = json!(cats
                .iter()
                .map(|(d, v)| json!({"week": d, "revenue": v}))
                .collect::<Vec<_>>());
            let axis_y = &mut payload_json["resolved"]["style"]["axis_y"];
            axis_y["ticks"]["visible"] = json!(visible);
            if let Some(len) = length {
                axis_y["ticks"]["length"] = json!(len);
            }
            payload_json["resolved"]["support_table"] = json!({
                "entries": [{"source": "revenue", "format": ",d", "label": "Units"}]
            });
            payload_json["resolved"]["effective_support_table_style"] = support_table_style_json();
            payload_json["width"] = json!(564.0);
            payload_json["height"] = json!(202.0);
            render(&make_payload(payload_json), &test_fonts()).expect("composed strip renders")
        };
        assert_left_axis_paints_and_reserves_the_visible_tick(render_with, 5);
    }

    #[test]
    fn rail_concat_left_value_axis_paints_and_anchors_labels_past_the_visible_tick() {
        use serde_json::json;
        let render_with = |visible: bool, length: Option<f64>| {
            let mut payload_json = make_rail_payload();
            let axis_y = &mut payload_json["resolved"]["style"]["axis_y"];
            axis_y["ticks"]["visible"] = json!(visible);
            if let Some(len) = length {
                axis_y["ticks"]["length"] = json!(len);
            }
            render(&make_payload(payload_json), &test_fonts()).expect("rail bar renders")
        };
        assert_left_axis_paints_and_reserves_the_visible_tick(render_with, 3);
    }

    #[test]
    fn rail_arm_right_value_axis_draws_a_visible_tick() {
        use serde_json::json;
        let render_with = |visible: bool| {
            let mut payload_json = make_rail_payload();
            payload_json["resolved"]["style"]["axis_y"]["position"] = json!("right");
            payload_json["resolved"]["style"]["axis_y"]["labels"]["align"] = json!("right");
            payload_json["resolved"]["style"]["axis_y"]["ticks"]["visible"] = json!(visible);
            render(&make_payload(payload_json), &test_fonts())
        };
        let ticked = render_with(true).expect("renders");
        let bare = render_with(false).expect("renders");
        assert_ne!(ticked, bare, "a visible tick changes the drawing");
    }

    /// Minimal date stepper for test fixtures — adds `days` to a Y-M-D date,
    /// Gregorian calendar. Test-only; production dates always come pre-formatted.
    fn chrono_like_date(mut year: i32, mut month: u8, mut day: u8, days: i64) -> String {
        let mut remaining = days;
        while remaining > 0 {
            day += 1;
            let dim = match month {
                1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
                4 | 6 | 9 | 11 => 30,
                _ => {
                    if (year % 4 == 0 && year % 100 != 0) || year % 400 == 0 {
                        29
                    } else {
                        28
                    }
                }
            };
            if day > dim {
                day = 1;
                month += 1;
                if month > 12 {
                    month = 1;
                    year += 1;
                }
            }
            remaining -= 1;
        }
        format!("{year:04}-{month:02}-{day:02}")
    }

    #[test]
    fn unsupported_orientation_errors() {
        use serde_json::json;
        let payload_json = json!({
            "resolved": {
                "link_open": "page",
                "id": "t", "title": null, "title_style": null, "background": "#ffffff", "canvas": "#ffffff",
                "x": "a", "y": "b", "color": null,
                "orientation": "diagonal",
                "stack": "none", "palette": [], "multiples": null,
                "field_labels": {}, "wide_measures": [], "layers": [],
                "legend": {"visible": false, "position": {"edge": "top", "align": "start", "overlay": false}, "direction": "horizontal", "title": {"font": {"case": "title"}}, "label": {"font": {}}},
                "style": {
                    "axis_x": {"grid": {"visible": false}, "ticks": {"color": "#D0D3D9"}, "labels": {"font": {}}, "title": {"font": {"case": "title"}}, "scale": {}, "tick_values": [], "fiscal_year_start_month": 1, "is_quantitative": true, "zero_anchored": false},
                    "axis_y": {"grid": {"visible": false}, "ticks": {"color": "#D0D3D9"}, "labels": {"font": {}}, "title": {"font": {"case": "title"}}, "scale": {}, "tick_values": [], "fiscal_year_start_month": 1, "is_quantitative": true, "zero_anchored": false},
                    "mark": {"band_width": 0.7, "size": 20.0, "border": {}},
                    "grouped": {"padding_inner": 0.2, "padding_outer": 0.2},
                    "single_series_fill": "#000",
                    "series_label": {"font_family": "'Inter'", "font_size": 11.0, "font_weight": "400", "font_style": "normal", "dark_companion_palette": [], "pin_companions": []},
                    "endpoint_labels": {"visible": false, "height": 20.0, "label_offset": 6.0}
                }
            },
            "rows": [],
            "width": 600.0, "height": 400.0,
            "background": "#ffffff", "fonts_dir": null, "padding": null
        });
        let payload: Payload = serde_json::from_value(payload_json).unwrap();
        let fonts = test_fonts();
        let err = render(&payload, &fonts).unwrap_err();
        assert!(err.contains("orientation"), "error names orientation: {err}");
    }

    // ── Stacking (stack = "zero" / "normalize") ──────────────────────────────

    /// Two categories ("A", "B") × two series ("x", "y"). Series "y" has the
    /// larger global sum (45 vs 15), so the default stack order puts "y" at
    /// the baseline. `stacked_domain_max` mirrors what resolve() always bakes
    /// for a real chart. `radius: 0.0` keeps marks plain `<rect>`s.
    fn make_stacked_payload(orientation: &str, stack: &str) -> serde_json::Value {
        use serde_json::json;

        let rows = json!([
            {"cat": "A", "series": "x", "val": 10.0},
            {"cat": "A", "series": "y", "val": 30.0},
            {"cat": "B", "series": "x", "val": 5.0},
            {"cat": "B", "series": "y", "val": 15.0},
        ]);
        let palette = json!(["#3164a3", "#779bc9"]); // x -> [0], y -> [1] (alphabetical)

        let axis = json!({
            "grid": {"visible": false, "threshold": null, "width": 1.0, "color": "#D0D3D9", "opacity": 1.0},
            "ticks": {"color": "#D0D3D9", "visible": true, "count": 6.0},
            "labels": {
                "font": {"family": TEST_FONT, "color": "#626366", "size": 11.0, "weight": "400"},
                "padding": 6.0, "min_gap": 4.0, "format": null, "visible": true, "align": "left"
            },
            "title": {"font": {"family": TEST_FONT, "weight": "600"}, "padding": 0.0, "visible": false},
            "position": "left", "mirror": null, "band_position": 0.5, "scale": {},
            "tick_values": [0.0, 25.0, 50.0], "domain_max": null, "domain_min": null,
            "ruler": null, "tick_label": {"format": ",.0~f"},
            "time_unit": null, "fiscal_year_start_month": 1, "is_quantitative": true, "zero_anchored": false, "fill": null, "line": null
        });

        // Production themes bake `ticks.visible: false` on the value axis.
        let mut axis_y = axis.clone();
        axis_y["ticks"]["visible"] = json!(false);

        let resolved = json!({
            "link_open": "page",
            "id": "stacked_test",
            "chart_type": "bar",
            "title": "Stacked Test",
            "title_style": {
                "font": {"family": TEST_FONT, "color": "#494A4C", "size": 18.0, "weight": 500.0, "case": "none"},
                "position": {"offset": 10.0}
            },
            "background": "#ffffff",
            "canvas": "#ffffff",
            "x": "cat",
            "y": "val",
            "color": "series",
            "orientation": orientation,
            "stack": stack,
            "palette": palette,
            "multiples": null,
            "field_labels": {}, "wide_measures": [],
            "layers": [],
            "legend": {
                "visible": false,
                "position": {"edge": "top", "align": "start", "overlay": false},
                "direction": "horizontal",
                "title": {"font": {"case": "title", "family": TEST_FONT}},
                "label": {"padding": 8.0, "font": {"family": TEST_FONT, "color": "#626366", "size": 11.0, "weight": "400"}}
            },
            "style": {
                "label_usable_ratio": 0.8,
                "axis_x": axis,
                "axis_y": axis_y,
                "mark": {
                    "band_width": 0.8,
                    "size": 20.0,
                    "border": {"width": null, "color": null, "radius": 0.0}
                },
                "grouped": {"padding_inner": 0.2, "padding_outer": 0.2},
                "single_series_fill": "#3164a3",
                "series_label": {"font_family": "'Inter'", "font_size": 11.0, "font_weight": "400", "font_style": "normal", "dark_companion_palette": [], "pin_companions": []},
                "endpoint_labels": {"visible": false, "height": 20.0, "label_offset": 6.0}
            },
            "stacked_domain_max": 50.0
        });

        json!({
            "resolved": resolved,
            "rows": rows,
            "width": 400.0,
            "height": 300.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": {"left": 16.0, "right": 16.0, "top": 16.0, "bottom": 16.0}
        })
    }

    /// Every `<rect x=...>` mark in the SVG, as `(x, y, width, height, fill)`.
    fn parse_rects(svg: &str) -> Vec<(f64, f64, f64, f64, String)> {
        let attr = |snippet: &str, name: &str| -> String {
            snippet
                .split(&format!("{name}=\""))
                .nth(1)
                .unwrap()
                .split('"')
                .next()
                .unwrap()
                .to_string()
        };
        svg.match_indices("<rect x=")
            .map(|(start, _)| {
                let snippet = &svg[start..(start + 300).min(svg.len())];
                (
                    attr(snippet, "x").parse().unwrap(),
                    attr(snippet, "y").parse().unwrap(),
                    attr(snippet, "width").parse().unwrap(),
                    attr(snippet, "height").parse().unwrap(),
                    attr(snippet, "fill"),
                )
            })
            .collect()
    }

    /// The distance between two categories' first grouped bars is the band
    /// step `W / (n - padding_inner + 2 * padding_outer)`, so it moves with
    /// each authored padding and tells the two apart.
    fn grouped_band_step(padding_inner: f64, padding_outer: f64) -> f64 {
        let mut payload_json = make_vertical_payload(&[("Jan", 100.0), ("Feb", 150.0)], true);
        payload_json["resolved"]["style"]["grouped"] =
            serde_json::json!({"padding_inner": padding_inner, "padding_outer": padding_outer});
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("grouped bar renders");
        // Bars are rounded `<path d="M x,..." fill="#3164a3">` marks; the first
        // series fills `#3164a3`.
        let first_series: Vec<f64> = svg
            .split("<path d=\"M ")
            .skip(1)
            .filter(|mark| mark.contains("fill=\"#3164a3\""))
            .map(|mark| mark.split(',').next().unwrap().parse().unwrap())
            .collect();
        assert_eq!(first_series.len(), 2, "{svg}");
        first_series[1] - first_series[0]
    }

    #[test]
    fn grouped_band_padding_follows_the_authored_inner_and_outer_values() {
        let default = grouped_band_step(0.2, 0.2);
        let inner_small = grouped_band_step(0.1, 0.4);
        let inner_large = grouped_band_step(0.4, 0.1);
        // step = W / (2 - inner + 2 * outer): 2.2, 2.7 and 1.8 share-widths of W.
        assert!((default / inner_small - 2.7 / 2.2).abs() < 0.01, "{default} {inner_small}");
        assert!((inner_large / inner_small - 2.7 / 1.8).abs() < 0.01, "{inner_large} {inner_small}");
    }

    /// Rebuild the stacked payload as production's color-1:1-with-x shape
    /// (`color: cat` — every category is its own single-segment "stack"),
    /// with row order picked so the stack order (descending sum: B before A)
    /// disagrees with the alphabetical color domain (A before B).
    fn make_1to1_payload(orientation: &str) -> serde_json::Value {
        use serde_json::json;
        let mut payload = make_stacked_payload(orientation, "zero");
        payload["rows"] = json!([
            {"cat": "B", "val": 30.0},
            {"cat": "A", "val": 10.0},
        ]);
        payload["resolved"]["color"] = json!("cat");
        payload["resolved"]["stacked_domain_max"] = json!(30.0);
        payload
    }

    /// #8118's carve-out: when color is 1:1 with the category (each band a
    /// single segment), production keys palette slots off the alphabetical
    /// color domain (`palette_order = series if _is_color_1to1_with_x else
    /// order`, emitters/bar.py) — NOT the baseline-first stack order the
    /// multi-series stacked tests above pin. Regression for
    /// charts/bar-charts_4 (Accessories/Electronics swapped colors).
    #[test]
    fn color_1to1_with_category_takes_alphabetical_palette_slots() {
        for orientation in ["vertical", "horizontal"] {
            let payload = make_payload(make_1to1_payload(orientation));
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("1:1 color renders");
            let rects = parse_rects(&svg);
            assert_eq!(rects.len(), 2, "{orientation}: one bar per category: {rects:?}");
            // The A bar is the smaller one (val 10 < 30) on either orientation.
            let (a, b) = if rects[0].2 * rects[0].3 < rects[1].2 * rects[1].3 {
                (&rects[0], &rects[1])
            } else {
                (&rects[1], &rects[0])
            };
            assert_eq!(a.4, "#3164a3", "{orientation}: A takes palette[0] (alphabetical): {rects:?}");
            assert_eq!(b.4, "#779bc9", "{orientation}: B takes palette[1] (alphabetical): {rects:?}");
        }
    }

    /// The legend-swatch arm follows the same 1:1 palette mapping as the
    /// segment fills (its display sequence stays the stack order — B, the
    /// larger sum, listed before A on the horizontal arm).
    #[test]
    fn color_1to1_legend_swatches_take_alphabetical_palette_slots() {
        use serde_json::json;
        let mut payload_json = make_1to1_payload("horizontal");
        payload_json["resolved"]["legend"]["visible"] = json!(true);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("1:1 legend renders");
        // Legend swatches are the LEGEND_SWATCH-sized squares; pair each with
        // the label that follows it in document order.
        let mut entries = Vec::new();
        for (pos, _) in svg.match_indices(&format!("width=\"{LEGEND_SWATCH}\"")) {
            let snippet = &svg[pos..(pos + 500).min(svg.len())];
            let fill = snippet.split("fill=\"").nth(1).unwrap().split('"').next().unwrap();
            let label = legend_label_text(snippet);
            entries.push((label.to_string(), fill.to_string()));
        }
        assert_eq!(
            entries,
            vec![
                ("B".to_string(), "#779bc9".to_string()),
                ("A".to_string(), "#3164a3".to_string()),
            ],
            "display order stays stack order (B first); swatch fills key off the alphabetical domain: {svg}"
        );
    }

    /// Two long series names, so the rail pane's own width separates the
    /// pane-0 wrap limit from the card limit.
    const RAIL_SERIES: [&str; 2] =
        ["Northwest Regional Operations", "Southeast Regional Operations"];
    /// Measures 416.5px at the subtitle's 11px slot — inside the 568px card
    /// limit, past the pane-0 limit (`avail_w − rail_pane_w − label_offset`),
    /// so it draws ONE line if the wrap basis is the card and TWO if it is
    /// the pane.
    const RAIL_SUBTITLE_LINE_1: &str =
        "Stacked volume by region against the prior year's own quarterly baseline";
    const RAIL_SUBTITLE_LINE_2: &str = "figure.";

    /// The RAIL CONCAT wraps at pane 0's own footprint, not the card:
    /// production re-runs `apply_title_overflow_to_spec` after
    /// `_correct_concat_overshoot` against `target_width − pane1_width −
    /// spacing − pad_l − pad_r` (`converters/chart.py`), because the title
    /// spans the pane's gutter + plot. The re-run keys on `"hconcat" in
    /// spec`, so bar's `right_pane` rail takes it exactly as line's does
    /// (`line.rs`'s `the_rail_arm_wraps_the_title_block_at_the_pane_limit_not_the_card`
    /// is the same test on the same expression).
    #[test]
    fn the_rail_arm_wraps_the_title_block_at_the_pane_limit_not_the_card() {
        use serde_json::json;
        let long = format!("{RAIL_SUBTITLE_LINE_1} {RAIL_SUBTITLE_LINE_2}");
        let build = |subtitle: &str| {
            let mut p = make_rail_payload();
            let [a, b] = RAIL_SERIES;
            p["rows"] = json!([
                {"cat": "A", "series": a, "val": 10.0},
                {"cat": "A", "series": b, "val": 30.0},
                {"cat": "B", "series": a, "val": 5.0},
                {"cat": "B", "series": b, "val": 15.0},
            ]);
            p["width"] = json!(600.0);
            p["resolved"]["subtitle"] = json!(subtitle);
            make_payload(p)
        };
        let fonts = test_fonts();
        // Discriminating premise: between the two limits, so only the pane
        // basis wraps.
        let rail_pane_w = crate::families::cartesian::rail_pane_width(
            &RAIL_SERIES.map(String::from),
            "'Inter'",
            11.0,
            "400",
            &fonts,
            600.0,
        )
        .unwrap();
        let card_limit = 600.0 - 16.0 - 16.0;
        let sub_w = fonts.measure(&long, "Inter", 11.0).unwrap();
        assert!(sub_w < card_limit, "fits the card at {sub_w}");
        // 6.0 is the fixture's `endpoint_labels.label_offset` — the concat
        // spacing production stamps.
        assert!(sub_w > card_limit - (rail_pane_w + 6.0), "outruns the pane limit");

        let wrapped = render(&build(&long), &fonts).expect("rail bar renders");
        let one_line = render(&build("Family: stacked"), &fonts).expect("rail bar renders");

        assert_two_subtitle_rows(&wrapped, RAIL_SUBTITLE_LINE_1, RAIL_SUBTITLE_LINE_2);
        assert_eq!(
            plot_group_top(&wrapped) - plot_group_top(&one_line),
            13.0,
            "the second line deepens the rail concat's title stack: {wrapped}"
        );
    }

    /// Rail fixture: vertical stacked with a left value axis, endpoint
    /// labels on, a subtitle, and baked series-label style — the shape
    /// production compiles to the right-pane hconcat rail.
    fn make_rail_payload() -> serde_json::Value {
        use serde_json::json;
        let mut payload = make_stacked_payload("vertical", "zero");
        payload["resolved"]["subtitle"] = json!("Family: stacked");
        // The resolved subtitle font every production payload carries — the
        // title block measures its own lines, so an unresolved family is a
        // fixture gap, not a shape this renderer meets.
        payload["resolved"]["title_style"]["subtitle"] =
            json!({"font": {"family": TEST_FONT, "size": 11.0, "weight": "400"}});
        payload["resolved"]["style"]["endpoint_labels"] =
            json!({"visible": true, "height": 20.0, "label_offset": 6.0});
        payload["resolved"]["style"]["series_label"] = json!({
            "font_family": "'Inter'", "font_size": 11.0, "font_weight": "400",
            "font_style": "normal", "dark_companion_palette": ["#111111", "#222222"], "pin_companions": [], "gap_px": 18.2
        });
        payload
    }

    /// Production crowds the rail's pane, not the card: raw `none` date labels that
    /// fit flat across the card but not across the narrower pane take the tilt the
    /// pane asks for.
    #[test]
    fn a_rail_tilts_raw_labels_that_only_the_narrower_pane_crowds() {
        use serde_json::json;
        let rows: Vec<serde_json::Value> = (1..=12)
            .flat_map(|m| {
                let day = format!("2024-{m:02}-01");
                [json!({"cat": day, "series": "x", "val": 10.0}), json!({"cat": day, "series": "y", "val": 30.0})]
            })
            .collect();
        let mut p = make_rail_payload();
        p["rows"] = json!(rows);
        p["width"] = json!(1000.0);
        let style = &mut p["resolved"]["style"];
        style["label_usable_ratio"] = json!(0.8);
        let labels = &mut style["axis_x"]["labels"];
        labels["time_unit"] = json!("none");
        labels["overlap"] = json!({"skip": false, "tilt": true});
        labels["tilt_increments"] = json!([0.0, -30.0, -45.0, -60.0, -90.0]);
        let svg = render(&make_payload(p.clone()), &test_fonts()).expect("renders");
        assert!(svg.contains("rotate("), "{svg}");
        p["width"] = json!(1100.0);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(!svg.contains("rotate("), "{svg}");
    }

    /// The rail arm: one label per series in the pane right of the plot,
    /// anchored at the LAST column's cumulative segment midpoints, painted
    /// in the companion ink of the series' palette slot. Fixture stacks:
    /// A: y 0..30, x 30..40; B: y 0..15, x 15..20 — last column is B, so
    /// y anchors at 7.5 and x at 17.5 (y's label sits below x's).
    #[test]
    fn vertical_stacked_rail_places_series_labels_at_segment_midpoints() {
        let payload = make_payload(make_rail_payload());
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("rail bar renders");

        let label = |needle: &str| -> (f64, f64, String) {
            let pos = svg.find(needle).unwrap_or_else(|| panic!("{needle} missing: {svg}"));
            let tag = &svg[svg[..pos].rfind("<text").unwrap()..pos];
            let attr = |name: &str| -> String {
                tag.split(&format!("{name}=\"")).nth(1).unwrap().split('"').next().unwrap().into()
            };
            (attr("x").parse().unwrap(), attr("y").parse().unwrap(), attr("fill"))
        };
        // Series names as rail labels (the stacked fixture's series are x/y).
        let (_, y_of_y, fill_y) = label(">y<");
        let (_, y_of_x, fill_x) = label(">x<");
        // Baseline series y (larger sum) takes palette slot 0 → companion 0.
        assert_eq!(fill_y, "#111111", "baseline series wears companion[0]: {svg}");
        assert_eq!(fill_x, "#222222", "outer series wears companion[1]: {svg}");
        // Value 7.5 (y) renders lower on screen than 17.5 (x).
        assert!(
            y_of_y > y_of_x,
            "y's midpoint (7.5) sits below x's (17.5): y at {y_of_y}, x at {y_of_x}\n{svg}"
        );
        // Subtitle rendered under the title.
        assert!(svg.contains(">Family: stacked<"), "subtitle present: {svg}");
    }

    /// The board slot governs the vertical rail's companion ink too
    /// (bar.rs's `series_ink` call inside `render_vertical`) — locally "y"
    /// (larger sum) would wear companion[0], but its board slot is 1.
    #[test]
    fn vertical_stacked_rail_ink_follows_the_board_slot() {
        use serde_json::json;
        let mut payload_json = make_rail_payload();
        payload_json["resolved"]["category_colors"] =
            json!([{"field": "series", "slots": {"x": 0, "y": 1}, "overrides": {}}]);
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("rail bar renders");
        let fill_of = |needle: &str| -> String {
            let pos = svg.find(needle).unwrap_or_else(|| panic!("{needle} missing: {svg}"));
            let tag = &svg[svg[..pos].rfind("<text").unwrap()..pos];
            tag.split("fill=\"").nth(1).unwrap().split('"').next().unwrap().to_string()
        };
        assert_eq!(fill_of(">y<"), "#222222", "y takes its board slot 1's companion: {svg}");
        assert_eq!(fill_of(">x<"), "#111111", "x takes its board slot 0's companion: {svg}");
    }

    /// S10: a legend kept alongside the rail off the right edge (`rail_concat`'s own
    /// `!legend_shown` conjunct) refuses the precise concat arm, but a
    /// grouped/stacked bar's endpoint labels must still draw — line/area
    /// already have this legacy fallback; before this fix `bar.rs` had none
    /// and the three series labels vanished entirely whenever the concat
    /// arm was refused for ANY reason.
    #[test]
    fn a_legend_kept_alongside_the_rail_still_draws_the_series_label_rail() {
        use serde_json::json;
        let mut payload_json = make_rail_payload();
        payload_json["resolved"]["legend"]["visible"] = json!(true);
        payload_json["resolved"]["legend"]["position"] = json!({"edge": "right", "align": "start", "overlay": false});
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("rail bar with a kept legend renders");
        // The legend also emits `>y<`/`>x<` text, so distinguish the rail by
        // its own companion ink (`vertical_stacked_rail_places_series_labels_
        // at_segment_midpoints`'s fill assertion) — a legend swatch/label
        // never paints in the dark-companion palette.
        assert!(
            svg.contains("fill=\"#111111\">y<") && svg.contains("fill=\"#222222\">x<"),
            "the series-label rail must still draw its two companion-ink labels when a legend \
             is kept: {svg}"
        );
    }

    /// The bar group's own translate x, plus the rightmost drawn bar's own
    /// right edge (`x + width` of the last `<rect>` inside that group) —
    /// the two together pin `plot_w` itself, the way `rail_strip_plot_w`
    /// does for line.rs's path endpoint.
    fn bar_group_x_and_rightmost_edge(svg: &str) -> (f64, f64) {
        let gi = svg.find("<g transform=\"translate(").expect("bar group present");
        let group_x: f64 = svg[gi..].split("translate(").nth(1).unwrap().split(',').next().unwrap().parse().unwrap();
        let grid_open = "<g class=\"mark-rule role-axis-grid\" aria-hidden=\"true\">";
        let mut body = gi + svg[gi..].find('>').unwrap() + 1;
        while svg[body..].starts_with(grid_open) {
            body += svg[body..].find("</g>").unwrap() + "</g>".len();
        }
        let group_end = svg[body..].find("</g>").map(|o| body + o).unwrap_or(svg.len());
        let rightmost = svg[gi..group_end]
            .match_indices("<rect x=\"")
            .map(|(i, _)| {
                let tag = &svg[gi + i..];
                let x: f64 = tag.split("x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
                let w: f64 = tag.split("width=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
                x + w
            })
            .fold(0.0_f64, f64::max);
        (group_x, rightmost)
    }

    /// S10: `rail_right_extra` reserves the theme's pane gap plus the
    /// widest rail label (`sl.gap_px + rail_w`) on the REFUSED-concat
    /// legacy path (a legend kept alongside the rail, same fixture as
    /// `a_legend_kept_alongside_the_rail_still_draws_the_series_label_rail`)
    /// — `Shape::RailConcat`'s own middle branch computes its own (already
    /// correct) geometry, so this reservation lives only in the final
    /// `else` a refusal falls into. The rail label itself draws at a FIXED
    /// `w − pad_r − rail_w` (never off `plot_right`), so without the
    /// reservation the bars grow to the plot's full width and land under
    /// the rail's own labels. Pinned exactly (a rail-off comparison payload
    /// isn't a fair mutation probe here: `endpoint_labels.visible: false`
    /// also flips `vega_fit`, routing through an unrelated layout
    /// function entirely). Mutation counterfactual: dropping the
    /// reservation (`rail_right_extra` always `0.0`) widens the bars and
    /// this pinned value moves.
    #[test]
    fn support_table_free_rail_reserves_its_own_column_from_the_plot() {
        use serde_json::json;
        let mut rail_on_json = make_rail_payload();
        rail_on_json["resolved"]["style"]["axis_y"]["position"] = json!("right");
        let svg = render(&make_payload(rail_on_json), &test_fonts()).expect("rail bar renders");
        let (group_x, rail_on_edge) = bar_group_x_and_rightmost_edge(&svg);
        let rail_label = &svg[..svg.find("fill=\"#111111\">y<").expect("a rail label")];
        let rail_x: f64 = rail_label
            .rsplit("<g transform=\"translate(")
            .next()
            .and_then(|t| t.split(',').next())
            .and_then(|x| x.parse().ok())
            .expect("the rail group's x");
        assert!(
            group_x + rail_on_edge <= rail_x,
            "the last bar's own right edge stays clear of the rail's fixed-position \
             labels (`w − pad_r − rail_w`): bar edge {} rail x {rail_x}\n{svg}",
            group_x + rail_on_edge
        );
    }

    /// The modeled rail-concat + strip sub-shape: `position: top`, left
    /// value axis, no legend — `region_bar_grouped`'s own shape.
    fn make_rail_strip_payload() -> serde_json::Value {
        use serde_json::json;
        let mut p = make_rail_payload();
        p["resolved"]["support_table"] = json!({
            "entries": [
                {"aggregate": "sum", "source": "val", "format": {"spec": "$,.0f", "raw": "currency_whole"}, "label": "Total $"},
                {"aggregate": "sum", "source": "val", "format": ",d", "label": "Units"}
            ]
        });
        p["resolved"]["effective_support_table_style"] = support_table_style_json();
        p
    }

    /// The `strip_h + ink_ext` bound a titled `position: top` legacy splice
    /// would need IF it modeled production's law — no longer what the arm
    /// actually computes (a titled top-strip on this arm now refuses, see
    /// `attach_support_table_strip`'s own doc comment) — kept only as the
    /// comparison bound the rail-concat/bottom-splice tests below still use
    /// to show their own arms grow by MORE, or by something DIFFERENT, than
    /// this formula. Verifying it against real production (two real charts
    /// probed with/without their support_table via `gs._prod_render`, one
    /// this exact 2-row/titled/kept-legend shape) found the true gap is NOT
    /// `strip_h + ink_ext` alone: the plot itself also grows by production's
    /// fit-vs-pad delta (91px on a 432-tall slot, reproduced on two
    /// unrelated shapes), a term this arm has no closed-form model for —
    /// which is why the arm refuses that shape now rather than shipping
    /// this formula's wrong height.
    fn bar_legacy_top_splice_canvas_h(payload_h: f64) -> f64 {
        // strip_h: padding_top(8) + row_h(14 + 2*2=18)*2 rows + padding_bottom(4) = 48.
        // ink_ext: -row_y_base(1, style, 0.0) + rise = (padding_bottom(4) +
        // row_h(18) + row_h/2(9)) + (round(0.8*11) - baseline_shift("middle",
        // 11)) = 31 + (9 - 3) = 37. Total 85.
        payload_h + 85.0
    }

    /// S7: on the modeled rail-concat sub-shape the strip splices into pane
    /// 0's own `autosize: pad`, so the canvas overshoots the slot by more
    /// than the legacy splice's own modest `+ strip_height` growth.
    /// Mutation counterfactual: reverting `plot_h` to the chrome-subtracted
    /// formula collapses the canvas back to (at most) the legacy bound.
    #[test]
    fn bar_support_table_on_the_rail_concat_grows_the_canvas_past_the_slot() {
        let payload_json = make_rail_strip_payload();
        let payload_h = payload_json["height"].as_f64().unwrap();
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("support_table rail concat renders");
        let (_, canvas_h) = {
            let attr = |name: &str| -> f64 {
                svg.split(&format!("{name}=\"")).nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
            };
            (attr("width"), attr("height"))
        };
        assert!(
            canvas_h > bar_legacy_top_splice_canvas_h(payload_h),
            "the canvas overshoots the slot by MORE than the legacy splice's own \
             `+ strip_height` growth: canvas_h={canvas_h} legacy_bound={}\n{svg}",
            bar_legacy_top_splice_canvas_h(payload_h)
        );
    }

    /// A TITLED variant of `make_rail_strip_payload` — the shape
    /// `region_bar_grouped`'s own corpus case actually takes.
    fn make_titled_rail_strip_payload() -> serde_json::Value {
        use serde_json::json;
        let mut p = make_rail_strip_payload();
        p["resolved"]["title"] = json!("Region Signups");
        p["resolved"]["title_style"]["font"] =
            json!({"family": TEST_FONT, "size": 18.0, "weight": "500", "color": "#494A4C"});
        p["resolved"]["title_style"]["position"] = json!({"offset": 10.0});
        p
    }

    /// MEDIUM-1, round 2: `make_rail_strip_payload`'s own inherited subtitle
    /// (`make_rail_payload`'s `"Family: stacked"`) must still draw on the
    /// rail+strip arm — `title_stack` already reserves its height through
    /// `ti.ink_h` regardless of which emission path runs, so a silent drop
    /// would reserve space it never painted. Regression for the old
    /// `emit_title_vega(pad_l, pad_t)` bypass, which drew the title only.
    #[test]
    fn bar_support_table_on_the_rail_concat_still_draws_the_subtitle() {
        let payload = make_payload(make_titled_rail_strip_payload());
        let svg = render(&payload, &test_fonts()).expect("titled support_table rail concat renders");
        assert!(svg.contains(">Family: stacked<"), "subtitle must still draw: {svg}");
    }

    /// `playground/support-table-attached/weekly_bar_grouped`'s shape: a
    /// stacked, colored bar's `per_series:` entry expands to one strip row
    /// per series, in the SAME order the endpoint rail's own labels take
    /// (`stack_order` — baseline series "y" first, per
    /// `vertical_stacked_rail_places_series_labels_at_segment_midpoints`),
    /// each row's cell the SUM of `val` grouped by (cat, series), the label
    /// the series name painted in the rail's own companion ink. Mutation
    /// counterfactual: reverting `per_series_order` to `color_domain`
    /// (alphabetical "x", "y") swaps the row order and fails the first
    /// assertion; dropping `label_color` threading paints every stub in the
    /// plain support_table ink and fails the second.
    #[test]
    fn bar_support_table_on_the_rail_concat_expands_a_per_series_entry() {
        use serde_json::json;
        let mut p = make_rail_payload();
        p["resolved"]["support_table"] = json!({"entries": [{"per_series": "val", "format": ",d"}]});
        p["resolved"]["effective_support_table_style"] = support_table_style_json();
        let payload = make_payload(p);
        let svg = render(&payload, &test_fonts()).expect("per_series rail concat renders");

        // The rail ALSO paints an "x"/"y" label of its own, earlier in the
        // markup than the strip — `rfind` picks the strip's own stub, the
        // last (and only other) occurrence.
        let stub = |label: &str| -> (usize, String) {
            let pos = svg.rfind(&format!(">{label}<")).unwrap_or_else(|| panic!("{label} stub missing: {svg}"));
            let tag = &svg[svg[..pos].rfind("<text").unwrap()..pos];
            let fill = tag.split("fill=\"").nth(1).unwrap().split('"').next().unwrap().to_string();
            (pos, fill)
        };
        let (y_pos, y_fill) = stub("y");
        let (x_pos, x_fill) = stub("x");
        assert!(y_pos < x_pos, "baseline series y's row sits closer to the plot than x's: {svg}");
        assert_eq!(y_fill, "#111111", "baseline series wears the SAME companion ink the rail paints");
        assert_eq!(x_fill, "#222222");
        // Each series' own cell: cat A/B sums (single row per (cat,series) —
        // sum over 1 row is that row's own value).
        assert!(svg.contains(">30<") && svg.contains(">15<"), "y's cells (A=30, B=15): {svg}");
        assert!(svg.contains(">10<") && svg.contains(">5<"), "x's cells (A=10, B=5): {svg}");
    }

    /// `playground/support-table-attached/weekly_bar_grouped`'s own shape,
    /// mirrored onto a LEFT axis: production's own
    /// `_endpoint_labels_visible_default` (bar.py:161-169) turns the rail off
    /// for a normal per_series entry ("the rail would name them a second
    /// time") and keeps a right legend on instead — but
    /// `_suppress_series_legend` then removes that legend at render time
    /// (`bar_support_table_per_series_suppresses_a_kept_legend` pins that
    /// law separately), so `legend_shown` is false here and `composed_cfg`'s
    /// gate admits this rail-disabled, color-carrying shape on either axis
    /// side (probed: `gs._prod_render` on the real corpus board — canvas,
    /// plot rect and stub match this arm's existing formula once
    /// `color_field.is_some()` is let through). Mutation counterfactual:
    /// reverting `legend_shown`'s `!has_per_series` re-derivation draws the
    /// legend and routes this shape back to the (still-refusing) legacy
    /// splice.
    #[test]
    fn bar_support_table_composed_arm_expands_a_per_series_entry_with_a_suppressed_legend() {
        use serde_json::json;
        let mut p = make_rail_payload();
        p["resolved"]["subtitle"] = serde_json::Value::Null;
        p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(false);
        p["resolved"]["legend"]["visible"] = json!(true);
        p["resolved"]["legend"]["position"] = json!({"edge": "right", "align": "start", "overlay": false});
        p["resolved"]["support_table"] = json!({"entries": [{"per_series": "val", "format": ",d"}]});
        p["resolved"]["effective_support_table_style"] = support_table_style_json();
        let payload = make_payload(p);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("left-axis per_series composed strip renders");

        let plot_top: f64 = svg
            .split("<g transform=\"translate(")
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
        // pad_t(16) + title_fs(18) + title_offset_theme(10) +
        // correction(18)=7 + ink_ext(2 rows: 37) = 88.
        assert!((plot_top - 88.0).abs() < 0.01, "plot_top: {plot_top}\n{svg}");

        let plot_left: f64 = svg
            .split("<g transform=\"translate(")
            .nth(1)
            .unwrap()
            .split(',')
            .next()
            .unwrap()
            .parse()
            .unwrap();
        let val_labels = ["0", "25", "50"];
        let max_val_w = val_labels
            .iter()
            .map(|s| fonts.measure(s, TEST_FONT, 11.0).expect("test font measures"))
            .fold(0.0_f64, f64::max);
        let val_gutter = 6.0 + max_val_w;
        // The stub reach: the wider of the two series-name labels ("x"/"y").
        let stub_w = fonts.measure("x", TEST_FONT, 11.0).expect("test font measures").max(
            fonts.measure("y", TEST_FONT, 11.0).expect("test font measures"),
        );
        let stub_reach = 6.0 + stub_w;
        // No zero rule on this fixture's axis (`grid.threshold: null`).
        // Whole-pixel snapped — see the sibling test's own comment.
        let expected_left = (16.0 + val_gutter.max(stub_reach).max(0.0)).ceil();
        assert!(
            (plot_left - expected_left).abs() < 0.01,
            "plot_left = pad_l + max(val_gutter, stub_reach), whole-pixel snapped: \
             got {plot_left}, expected {expected_left}\n{svg}"
        );
        assert!(svg.contains(">30<") && svg.contains(">15<"), "y's cells (A=30, B=15): {svg}");
        assert!(svg.contains(">10<") && svg.contains(">5<"), "x's cells (A=10, B=5): {svg}");
    }

    /// Production's per_series render-time legend suppression
    /// (`_suppress_series_legend`, `support_table_attachment.py:4408-4446`)
    /// runs AFTER the base spec — legend included — is already built, so
    /// `resolved.legend.visible` is stale for this one shape:
    /// `render_vertical`'s `legend_shown &= !has_per_series` re-derives it.
    /// Mutation counterfactual: dropping
    /// `&& !has_per_series` from `legend_shown` draws the legend swatch
    /// production removes and fails this test (it cannot fail any
    /// non-per_series test — `has_per_series` is false there, so
    /// `legend_shown` is bit-identical).
    #[test]
    fn bar_support_table_per_series_suppresses_a_kept_legend() {
        use serde_json::json;
        let mut p = make_rail_payload();
        p["resolved"]["subtitle"] = serde_json::Value::Null;
        p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(false);
        p["resolved"]["legend"]["visible"] = json!(true);
        p["resolved"]["legend"]["position"] = json!({"edge": "right", "align": "start", "overlay": false});
        p["resolved"]["support_table"] = json!({"entries": [{"per_series": "val", "format": ",d"}]});
        p["resolved"]["effective_support_table_style"] = support_table_style_json();
        let payload = make_payload(p);
        let svg = render(&payload, &test_fonts()).expect("top-strip per_series with a kept legend renders");
        // `emit_vertical_legend` (a right legend's symbol) draws a circle
        // via `<path transform="translate(...)">` — a mark tag no bar
        // rect/rule/text emission uses, so its absence is a clean signal
        // the legend column was never drawn.
        assert!(
            !svg.contains("<path transform=\"translate("),
            "production nulls the legend for a per_series entry: {svg}"
        );
    }

    /// A legend over authored columns beside a strip takes the pad arm.
    #[test]
    fn a_vertical_legend_over_columns_beside_a_strip_renders() {
        use serde_json::json;
        let mut p = make_rail_payload();
        p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(false);
        p["resolved"]["legend"]["visible"] = json!(true);
        p["resolved"]["legend"]["position"] = json!({"edge": "right", "align": "start", "overlay": false});
        p["resolved"]["legend"]["direction"] = json!("vertical");
        p["resolved"]["legend"]["columns"] = json!(2);
        p["resolved"]["support_table"] =
            json!({"entries": [{"source": "val", "aggregate": "sum", "format": {"spec": ",d", "raw": "integer"}, "label": "Val"}]});
        p["resolved"]["effective_support_table_style"] = support_table_style_json();
        let svg = render(&make_payload(p), &test_fonts()).expect("columns take the pad arm");
        assert!(svg.contains(">Val<"), "{svg}");
    }

    /// A GROUPED (non-stacked) bar's per_series entry takes production's
    /// distinct single-column law (`_per_series_column_header`,
    /// support_table_attachment.py:998-1008 — "the series themselves are
    /// named by the chart's own legend"), which this renderer doesn't
    /// model. `stack_order` (this file's `series_order` source) is only
    /// ever built `if is_stacked`, so a grouped bar's `per_series_order` is
    /// always empty and `visual_row_count`'s own `series_count > 0` guard
    /// refuses by name before any strip row is built — never a
    /// silently-wrong one-row-per-series render. Mutation counterfactual:
    /// seeding `per_series_order` from `color_domain` instead of
    /// `stack_order` would make this render (wrongly) instead of refusing.
    #[test]
    fn bar_support_table_grouped_bar_per_series_entry_is_rejected() {
        use serde_json::json;
        let mut p = make_stacked_payload("vertical", "none");
        p["resolved"]["support_table"] = json!({"entries": [{"per_series": "val", "format": ",d"}]});
        p["resolved"]["effective_support_table_style"] = support_table_style_json();
        let payload = make_payload(p);
        let err = render(&payload, &test_fonts()).unwrap_err();
        assert!(err.contains("per_series"), "names the unsupported shape: {err}");
        assert!(err.contains("series_count"), "names the missing input: {err}");
    }

    fn bar_group_y(svg: &str) -> f64 {
        let i = svg.find("<g transform=\"translate(").expect("bar group present");
        svg[i..].split(',').nth(1).unwrap().split(')').next().unwrap().parse().unwrap()
    }

    fn bar_canvas_dims(svg: &str) -> (f64, f64) {
        let attr = |name: &str| -> f64 {
            svg.split(&format!("{name}=\"")).nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        (attr("width"), attr("height"))
    }

    /// S3 pinned exactly, both families' HIGH-1 regression: the strip
    /// pad-anchors the title at the canvas padding edge (`bar.rs` already
    /// takes the `emit_title_vega(pad_l, pad_t)` branch — this is the
    /// regression test line.rs was missing). Mutation counterfactual: M4
    /// (`strip_title_offset` -> `ti.offset`) shrinks `top_margin` and moves
    /// `group_y`.
    #[test]
    fn bar_support_table_on_the_rail_concat_pins_exact_canvas_and_group_y_titled() {
        let payload = make_payload(make_titled_rail_strip_payload());
        let svg = render(&payload, &test_fonts()).expect("titled support_table rail concat renders");
        assert_eq!(bar_canvas_dims(&svg), (400.0, 441.0), "{svg}");
        assert_eq!(bar_group_y(&svg), 102.0, "{svg}");
    }

    /// The untitled sibling (`make_rail_strip_payload`'s own default title
    /// cleared): the concat spec carries no padding bump for the strip, so only
    /// the strip's ink sets `top_margin`.
    #[test]
    fn bar_support_table_on_the_rail_concat_pins_exact_canvas_and_group_y_untitled() {
        use serde_json::json;
        let mut payload_json = make_rail_strip_payload();
        payload_json["resolved"]["title"] = json!("");
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("untitled support_table rail concat renders");
        assert_eq!(bar_canvas_dims(&svg), (400.0, 392.0), "{svg}");
        assert_eq!(bar_group_y(&svg), 53.0, "{svg}");
    }

    /// A fractional row pitch leaves a fraction in the strip's ink, but the
    /// margin above the plot is one ceil'd overhang (`ViewLayout.js:165-168`),
    /// so the rail concat's plot origin is a whole pixel in both forms.
    #[test]
    fn bar_support_table_on_the_rail_concat_origin_is_the_ceiled_top_margin() {
        use serde_json::json;
        for titled in [true, false] {
            let mut payload_json =
                if titled { make_titled_rail_strip_payload() } else { make_rail_strip_payload() };
            if !titled {
                payload_json["resolved"]["title"] = json!("");
            }
            payload_json["resolved"]["effective_support_table_style"]["row"]["padding"]["vertical"] = json!(2.1);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            let group_y = bar_group_y(&svg);
            assert_eq!(group_y.fract(), 0.0, "titled={titled}: origin is a whole pixel: {svg}");
        }
    }

    /// Vega's `autosize: pad` adds the plot height verbatim between two margins
    /// that are each ceil'd on their own (`ViewLayout.js:165-168`, `:198-201`;
    /// `vega_fit::pad_plot`), so a fractional plot height stays fractional in
    /// the canvas. The production canvas at spec height 261, 261.3333 and
    /// 261.9 of `region_bar_grouped` is 401, 401.3333 and 401.9, titled or not.
    #[test]
    fn bar_support_table_on_the_rail_concat_keeps_a_fractional_plot_height_in_the_canvas() {
        use serde_json::json;
        for titled in [true, false] {
            let mut base_json =
                if titled { make_titled_rail_strip_payload() } else { make_rail_strip_payload() };
            if !titled {
                base_json["resolved"]["title"] = json!("");
            }
            let h = base_json["height"].as_f64().unwrap();
            let whole = bar_canvas_dims(
                &render(&make_payload(base_json.clone()), &test_fonts()).expect("renders"),
            )
            .1;
            for frac in [0.3333, 0.5, 0.9] {
                let mut payload_json = base_json.clone();
                payload_json["height"] = json!(h + frac);
                let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
                let canvas_h = bar_canvas_dims(&svg).1;
                assert!(
                    (canvas_h - (whole + frac)).abs() < 1e-9,
                    "titled={titled}: canvas {canvas_h} against {} at plot height {}",
                    whole + frac,
                    h + frac
                );
            }
        }
    }

    /// S6/S8 left-hang: a stub label wider than the axis-only gutter widens
    /// `y_extent` on bar's own rail+strip arm too — `region_bar_grouped`'s
    /// own "Top cat" stub is the corpus case, so leaving this term unpinned
    /// (round 2's finding) leaves the ONE shape it exists for undetected.
    /// Mutation counterfactual: dropping the `y_extent.max(..)` term
    /// collapses `left_margin` back to the axis-only extent and both
    /// assertions fail.
    #[test]
    fn bar_support_table_on_the_rail_concat_widens_y_extent_for_a_wide_stub() {
        use serde_json::json;
        let mut narrow = make_rail_strip_payload();
        narrow["resolved"]["support_table"]["entries"][0]["label"] = json!("$");
        narrow["resolved"]["support_table"]["entries"][1]["label"] = json!("N");
        let svg = render(&make_payload(narrow), &test_fonts()).expect("renders");
        let (narrow_group_x, _) = bar_group_x_and_rightmost_edge(&svg);

        let mut wide = make_rail_strip_payload();
        wide["resolved"]["support_table"]["entries"][0]["label"] =
            json!("Average Order Revenue Per Signup");
        let svg2 = render(&make_payload(wide), &test_fonts()).expect("renders");
        let (wide_group_x, _) = bar_group_x_and_rightmost_edge(&svg2);

        assert!(
            wide_group_x > narrow_group_x,
            "a stub past the axis gutter widens left_margin by the capped label \
             width: narrow={narrow_group_x} wide={wide_group_x}\n{svg}\n{svg2}"
        );
    }

    /// S1/S7 on bar, mirroring line's: pane 0 is under `autosize: pad`, whose
    /// title law pad-anchors the title at the canvas padding edge —
    /// `title.offset` moves the PLOT down (S3), never the title. Mutation
    /// counterfactual: reverting the strip branch of `title_pos` to the
    /// non-strip closed form (`-y_extent_ink, -top_ink - ink_h - offset`)
    /// moves the title 85px and fails this.
    #[test]
    fn bar_support_table_on_the_rail_concat_pad_anchors_the_title() {
        let payload_json = make_titled_rail_strip_payload();
        let pad_t = payload_json["padding"]["top"].as_f64().unwrap();
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("titled support_table rail concat renders");
        let pos = svg.find(">Region Signups<").expect("title text present");
        let tag_start = svg[..pos].rfind("<text").unwrap();
        let y: f64 = svg[tag_start..pos].split("y=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
        assert_eq!(y, pad_t + measure::baseline_shift("top", 18.0), "title pad-anchors: {svg}");
    }

    /// S8, both directions: the strip's own right-reaching cell competes
    /// with the zero rule's stroke for pane 0's own right bound, taking the
    /// `max`. `make_rail_strip_payload`'s own default currency cells are too
    /// narrow to overhang the pane at all (its own comment: "no swept case
    /// binds") — a narrowed slot plus widened row values here make the cell
    /// win instead; a thick `ax_y.grid.threshold.width` flips the winner to the
    /// rule. Pinned exactly (a relative `<` comparison between two variants
    /// is not a fair mutation probe: dropping the reservation can move BOTH
    /// sides, leaving the inequality's direction, though not its values,
    /// intact). Mutation counterfactual: dropping
    /// `.max(composed_right_overhang(..))` collapses the strip-wins case
    /// back to the rule-wins case's own edge, and this assertion fails.
    /// `strip_wins_edge` moved from 102.6 to 101.65 porting BR1 (the rail's
    /// two-pass `ceil` law) — pane 0 is 1px narrower than the old one-shot
    /// `round` gave, so every band-scaled edge inside it moves with it;
    /// `rule_wins_edge` is unaffected (its `right_ink`(100) so dwarfs pane 0
    /// that both the old and new formulas ceil to the same plot width).
    #[test]
    fn bar_support_table_on_the_rail_concat_unions_the_strip_s_right_ink_with_the_zero_rule_s() {
        use serde_json::json;
        let mut wide_cells = make_rail_strip_payload();
        wide_cells["width"] = json!(200.0);
        wide_cells["rows"] = json!([
            {"cat": "A", "series": "x", "val": 400000000000.0},
            {"cat": "A", "series": "y", "val": 900000000000.0},
            {"cat": "B", "series": "x", "val": 200000000000.0},
            {"cat": "B", "series": "y", "val": 800000000000.0},
        ]);
        let svg = render(&make_payload(wide_cells.clone()), &test_fonts()).expect("renders");
        let (_, strip_wins_edge) = bar_group_x_and_rightmost_edge(&svg);

        let mut thick = wide_cells;
        thick["resolved"]["style"]["axis_y"]["grid"]["visible"] = json!(true);
        thick["resolved"]["style"]["axis_y"]["grid"]["threshold"] = json!({"width": 100.0});
        let svg2 = render(&make_payload(thick), &test_fonts()).expect("renders");
        let (_, rule_wins_edge) = bar_group_x_and_rightmost_edge(&svg2);
        assert!(
            (strip_wins_edge - 101.65).abs() < 0.01,
            "the strip's own cell wins: strip_wins_edge={strip_wins_edge}\n{svg}"
        );
        assert!(
            (rule_wins_edge - 8.55).abs() < 0.01,
            "a thick zero rule's own ink wins instead: rule_wins_edge={rule_wins_edge}\n{svg2}"
        );

        assert!(
            rule_wins_edge < strip_wins_edge,
            "a thick zero rule's own ink wins and narrows the plot further: \
             strip_wins={strip_wins_edge} rule_wins={rule_wins_edge}\n{svg}\n{svg2}"
        );
    }

    /// S9's left-axis carve-out on bar's own rail arm: a trimmed (`$,.0f`)
    /// symbol row cannot anchor on a left-oriented value axis, so every cell
    /// keeps its own `$` (`region_bar_grouped`'s own "Total" row).
    #[test]
    fn bar_support_table_on_the_rail_concat_never_anchors_a_trimmed_symbol_row() {
        let payload = make_payload(make_rail_strip_payload());
        let svg = render(&payload, &test_fonts()).expect("support_table rail concat renders");
        // aggregate: sum over both series — cat A: 10+30=40, cat B: 5+15=20.
        assert!(svg.contains(">$40<"), "the would-be anchor cell keeps its own $: {svg}");
        assert!(svg.contains(">$20<"), "every OTHER cell keeps its $ too — no anchor: {svg}");
    }

    /// A bottom strip under the rail-concat arm grows the canvas below the axis.
    #[test]
    fn bar_support_table_position_bottom_on_a_rail_grows_the_canvas() {
        let mut payload_json = make_rail_strip_payload();
        payload_json["resolved"]["effective_support_table_style"] = support_table_style_json_bottom();
        payload_json["resolved"]["support_table_axis_offset"] = serde_json::json!(17.0);
        let top = render(&make_payload(make_rail_strip_payload()), &test_fonts()).expect("top strip renders");
        let bottom = render(&make_payload(payload_json), &test_fonts()).expect("bottom strip renders");
        assert!(bar_group_y(&bottom) < bar_group_y(&top), "a bottom strip leaves the plot's top where it was: {bottom}");
        assert!(bar_canvas_dims(&bottom).1 > bar_group_y(&bottom), "{bottom}");
    }

    /// A crowded monthly date x under a bottom strip beside an endpoint rail
    /// tilts at the rail-reduced layout width, and renders at every width.
    #[test]
    fn a_tilted_date_x_on_a_rail_strip_renders() {
        use serde_json::json;
        let mut p = make_rail_strip_payload();
        p["resolved"]["effective_support_table_style"]["position"] = json!("bottom");
        p["resolved"]["support_table_axis_offset"] = json!(17.0);
        p["resolved"]["style"]["axis_x"]["labels"]["overlap"] = json!({"skip": false, "tilt": true});
        p["resolved"]["style"]["axis_x"]["labels"]["tilt_increments"] = json!([30.0, 45.0, 90.0]);
        p["resolved"]["style"]["label_usable_ratio"] = json!(0.9);
        let rows: Vec<_> = (0..24)
            .flat_map(|i| {
                let day = format!("{}-{:02}-01", 2022 + i / 12, i % 12 + 1);
                [json!({"cat": day, "series": "x", "val": 10.0}), json!({"cat": day, "series": "y", "val": 30.0})]
            })
            .collect();
        p["rows"] = json!(rows);
        let outcomes: Vec<Result<usize, String>> = [260.0, 300.0, 340.0, 400.0, 480.0, 560.0]
            .iter()
            .map(|&w| {
                let mut q = p.clone();
                q["width"] = json!(w);
                render(&make_payload(q), &test_fonts()).map(|svg| svg.len()).map_err(|e| e.to_string())
            })
            .collect();
        assert!(outcomes.iter().all(Result::is_ok), "{outcomes:?}");
    }

    /// A right value axis with an active rail mirrors the rail-concat arm: the
    /// stub column hangs past the axis at the spec width.
    #[test]
    fn bar_support_table_right_axis_rail_renders_on_the_rail_concat() {
        use serde_json::json;
        let mut payload_json = make_rail_strip_payload();
        payload_json["resolved"]["style"]["axis_y"]["position"] = json!("right");
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("right-axis rail with a strip renders");
        assert!(svg.contains(">Units<"), "{svg}");
    }

    /// A legend kept beside an active rail sits inside pane 0, ahead of the rail.
    #[test]
    fn bar_support_table_legend_kept_alongside_rail_renders_on_the_rail_concat() {
        use serde_json::json;
        let mut payload_json = make_rail_strip_payload();
        payload_json["resolved"]["legend"]["visible"] = json!(true);
        payload_json["resolved"]["legend"]["position"] = json!({"edge": "right", "align": "start", "overlay": false});
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("rail with a kept legend and a strip renders");
        assert!(svg.contains(">Units<"), "{svg}");
    }

    /// The grouped bar rail runs the same label cascade as the line rail: a label too wide for one
    /// line wraps with the value leading, and one too long for two keeps its value and cuts line two.
    #[test]
    fn a_grouped_bar_rail_wraps_then_drops_the_value() {
        let _locale = crate::time_locale::en_us_scope();
        let render_named = |name: &str| {
            let mut p = make_time_series_payload("none");
            p["resolved"]["style"]["endpoint_labels"] = serde_json::json!({"visible": true, "label_offset": 6.0, "height": 20.0});
            p["resolved"]["style"]["series_label"]["gap_px"] = serde_json::json!(8.0);
            for row in p["rows"].as_array_mut().unwrap() {
                if row["series"] == "x" {
                    row["series"] = serde_json::json!(name);
                } else {
                    // Far enough from `x` that the stack has room for a taller label.
                    row["revenue"] = serde_json::json!(300.0);
                }
            }
            crate::diagnostic::collect(|| render(&make_payload(p), &test_fonts()).expect("renders"))
        };
        let (wrapped, warnings) = render_named("Northern Operations Division");
        assert!(wrapped.contains("<tspan>10 Northern</tspan>") && wrapped.contains("dy=\"13\">Operations Division</tspan>"), "{wrapped}");
        assert!(warnings.iter().all(|w| w.code != "WARN-RAIL-VALUE-DROPPED" && w.code != "WARN-SERIES-LABEL-TRUNCATED"), "{warnings:?}");
        let long = "Northwest Regional Operations and Field Support Division for the Greater Metropolitan Area and Surrounding Counties Coordination Office";
        let (dropped, warnings) = render_named(long);
        assert!(dropped.contains("<tspan>10 Northwest Regional</tspan>") && dropped.contains("\u{2026}</tspan>"), "values stay, line two is cut: {dropped}");
        assert!(warnings.iter().all(|w| w.code != "WARN-RAIL-VALUE-DROPPED"), "{warnings:?}");
        assert!(warnings.iter().any(|w| w.code == "WARN-SERIES-LABEL-TRUNCATED"), "{warnings:?}");
    }

    /// Each series anchors at its OWN last non-null column, never one
    /// shared trailing column: with series x absent from category B, x's
    /// label anchors in A's stack (midpoint 35 — above y's B-column 7.5).
    #[test]
    fn rail_anchors_at_each_series_own_last_nonnull_column() {
        use serde_json::json;
        let mut payload_json = make_rail_payload();
        payload_json["rows"] = json!([
            {"cat": "A", "series": "x", "val": 10.0},
            {"cat": "A", "series": "y", "val": 30.0},
            {"cat": "B", "series": "y", "val": 15.0},
        ]);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("rail bar renders");
        let y_pos = |needle: &str| -> f64 {
            let pos = svg.find(needle).unwrap_or_else(|| panic!("{needle} missing: {svg}"));
            let tag = &svg[svg[..pos].rfind("<text").unwrap()..pos];
            tag.split("y=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        assert!(
            y_pos(">x<") < y_pos(">y<"),
            "x anchors at its own A-column midpoint (35), above y's B-column 7.5: {svg}"
        );
    }

    /// The label cascade packs against production's RAW stacked extent
    /// (`_stacked_y_domain`: max column total, no headroom, no nice), never
    /// the rendered domain. Fixture: two 44-tall single-series columns put
    /// both anchors at 22 — the mean sits exactly at the raw midpoint
    /// (44/2), so production packs DESCENDING (labels nudge down, below the
    /// segment midpoint); against the rendered domain (stacked_domain_max
    /// 50, niced to 50 → midpoint 25) the mean would fall under the
    /// midpoint and the crowded label would be pushed UP into the headroom.
    #[test]
    fn rail_cascade_bounds_are_the_raw_stacked_extent_not_the_rendered_domain() {
        use serde_json::json;
        let mut payload_json = make_rail_payload();
        payload_json["rows"] = json!([
            {"cat": "A", "series": "x", "val": 44.0},
            {"cat": "B", "series": "y", "val": 44.0},
        ]);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("rail bar renders");

        // Any bar spans data 0..44, so its vertical midpoint is position(22).
        let rects = parse_rects(&svg);
        let (_, ry, _, rh, _) = rects[0].clone();
        let mid_y = ry + rh / 2.0;

        let label_y = |needle: &str| -> f64 {
            let pos = svg.find(needle).unwrap_or_else(|| panic!("{needle} missing: {svg}"));
            let tag = &svg[svg[..pos].rfind("<text").unwrap()..pos];
            tag.split("y=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        let (y1, y2) = (label_y(">x<"), label_y(">y<"));
        // Descending pack: both labels sit at or below the anchors' shared
        // midpoint (the baseline shift only moves them further down); an
        // ascending pack would hoist one ~18px above it into the headroom.
        assert!(
            y1 >= mid_y - 1.0 && y2 >= mid_y - 1.0,
            "crowded rail labels nudge DOWN from the raw-extent midpoint \
             (labels at {y1}/{y2}, segment mid {mid_y}): {svg}"
        );
        // And the cascade actually acted: the pair is spread by gap_px.
        assert!(
            (y1 - y2).abs() >= 17.0,
            "cascade spreads the coincident anchors by gap_px: {y1}/{y2}\n{svg}"
        );
    }

    #[test]
    fn vertical_stack_zero_puts_largest_series_sum_at_baseline() {
        let payload = make_payload(make_stacked_payload("vertical", "zero"));
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("stacked vertical renders");

        let rects = parse_rects(&svg);
        assert_eq!(rects.len(), 4, "2 categories × 2 series: {rects:?}");

        // Category "A": total 40, "y" (sum 45) sits at the baseline, "x"
        // (sum 15) stacks on top of it. The baseline segment's bottom edge
        // (y + height) must equal the *other* segment's top edge (y).
        let cat_a: Vec<_> = rects.iter().filter(|(x, ..)| *x < 40.0).collect();
        assert_eq!(cat_a.len(), 2, "category A has 2 segments: {cat_a:?}");
        let (base, top) = if cat_a[0].1 > cat_a[1].1 { (cat_a[0], cat_a[1]) } else { (cat_a[1], cat_a[0]) };
        assert!(
            (top.1 + top.3 - base.1).abs() < 0.1,
            "outer segment's bottom touches the baseline segment's top: {base:?} / {top:?}"
        );
        // "y" (baseline, larger raw value 30) is taller than "x" (10).
        assert!(base.3 > top.3, "baseline segment (y, val=30) taller than (x, val=10)");
        assert_eq!(base.4, "#3164a3", "baseline series receives palette[0]");
        assert_eq!(top.4, "#779bc9", "outer series receives palette[1]");
    }

    /// The board plans `category_colors` once per board (#7167): a value's
    /// slot wins over this chart's own baseline-first stack order. Locally
    /// "y" (sum 45) is the baseline and would take palette[0] — the board
    /// plan gives "x" slot 0 and "y" slot 1 instead. Paint order (which
    /// series sits at the baseline) is unaffected — only which color it
    /// gets — mirroring `category_scale_for` + `spatial_color_scale`.
    #[test]
    fn vertical_stacked_board_slot_wins_over_local_baseline_first_order() {
        let mut payload_json = make_stacked_payload("vertical", "zero");
        payload_json["resolved"]["category_colors"] =
            serde_json::json!([{"field": "series", "slots": {"x": 0, "y": 1}, "overrides": {}}]);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("stacked vertical renders");

        let rects = parse_rects(&svg);
        assert_eq!(rects.len(), 4, "2 categories × 2 series: {rects:?}");
        let cat_a: Vec<_> = rects.iter().filter(|(x, ..)| *x < 40.0).collect();
        let (base, top) = if cat_a[0].1 > cat_a[1].1 { (cat_a[0], cat_a[1]) } else { (cat_a[1], cat_a[0]) };
        assert!(base.3 > top.3, "baseline is still \"y\" (paint order unaffected): {base:?} / {top:?}");
        assert_eq!(base.4, "#779bc9", "baseline series (y) takes its board slot 1: {svg}");
        assert_eq!(top.4, "#3164a3", "outer series (x) takes its board slot 0: {svg}");
    }

    /// Same law on the grouped (stack = "none") shape, whose fill lookup is
    /// a separate code path (`color_domain` keyed by enumerate index, not
    /// `palette_domain`) from the stacked one above.
    #[test]
    fn vertical_grouped_board_slot_wins_over_local_alphabetical_order() {
        let mut payload_json = make_stacked_payload("vertical", "none");
        payload_json["resolved"]["category_colors"] =
            serde_json::json!([{"field": "series", "slots": {"x": 1, "y": 0}, "overrides": {}}]);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("grouped vertical renders");

        let mut rects = parse_rects(&svg);
        assert_eq!(rects.len(), 4, "2 categories × 2 series: {rects:?}");
        // "x" is the left sub-band (si=0) within each category cluster, "y"
        // the right one (si=1) — sorted by x this gives xA, yA, xB, yB.
        rects.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap());
        assert_eq!(
            rects.iter().map(|r| r.4.as_str()).collect::<Vec<_>>(),
            vec!["#779bc9", "#3164a3", "#779bc9", "#3164a3"],
            "x takes its board slot 1, y its board slot 0, in every category: {svg}"
        );
    }

    /// A grouped bar's legend has no stack order to pin to — its display
    /// order defaults to `color_domain` (alphabetical), UNLESS
    /// `style.legend.values` authors a full reordering of that same set,
    /// which wins outright (`emitters/bar.py`'s `order = list(chart.
    /// legend.values)` gate). Board-slot fills still apply underneath.
    #[test]
    fn grouped_legend_honors_an_authored_full_reordering_over_alphabetical() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("vertical", "none");
        payload_json["resolved"]["legend"]["visible"] = json!(true);
        payload_json["resolved"]["legend"]["values"] = json!(["y", "x"]);
        payload_json["resolved"]["category_colors"] =
            json!([{"field": "series", "slots": {"x": 1, "y": 0}, "overrides": {}}]);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        let mut entries = Vec::new();
        for (pos, _) in svg.match_indices(&format!("width=\"{LEGEND_SWATCH}\"")) {
            let snippet = &svg[pos..(pos + 500).min(svg.len())];
            let fill = snippet.split("fill=\"").nth(1).unwrap().split('"').next().unwrap();
            let label = legend_label_text(snippet);
            entries.push((label.to_string(), fill.to_string()));
        }
        assert_eq!(
            entries,
            vec![("y".to_string(), "#3164a3".to_string()), ("x".to_string(), "#779bc9".to_string())],
            "authored order (y, x) wins over alphabetical (x, y); fills follow the board slot: {svg}"
        );
    }

    /// `grouped_legend_domain` must fold-match case and `-`/`_`/space
    /// differences the same way production's `resolve_legend_entries`
    /// does, not require a BYTE-IDENTICAL full reordering
    /// (`sorted_values == sorted_domain`). An authored `["Y", "X"]`
    /// against a `["x", "y"]` domain is a full reorder once folded.
    #[test]
    fn grouped_legend_honors_a_case_folded_full_reordering() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("vertical", "none");
        payload_json["resolved"]["legend"]["visible"] = json!(true);
        payload_json["resolved"]["legend"]["values"] = json!(["Y", "X"]);
        payload_json["resolved"]["category_colors"] =
            json!([{"field": "series", "slots": {"x": 1, "y": 0}, "overrides": {}}]);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        let mut entries = Vec::new();
        for (pos, _) in svg.match_indices(&format!("width=\"{LEGEND_SWATCH}\"")) {
            let snippet = &svg[pos..(pos + 500).min(svg.len())];
            let fill = snippet.split("fill=\"").nth(1).unwrap().split('"').next().unwrap();
            let label = legend_label_text(snippet);
            entries.push((label.to_string(), fill.to_string()));
        }
        assert_eq!(
            entries,
            vec![("y".to_string(), "#3164a3".to_string()), ("x".to_string(), "#779bc9".to_string())],
            "fold-matched authored order (Y, X -> y, x) wins over alphabetical (x, y): {svg}"
        );
    }

    /// The board slot governs the FILL only — the legend's DISPLAY order
    /// stays production's own `reversed(stack_order)` for a vertical stacked
    /// bar (`apply_legend_entry_order`), never the board's slot order.
    #[test]
    fn vertical_stacked_legend_display_order_ignores_the_board_slot() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("vertical", "zero");
        payload_json["resolved"]["legend"]["visible"] = json!(true);
        payload_json["resolved"]["category_colors"] =
            json!([{"field": "series", "slots": {"x": 0, "y": 1}, "overrides": {}}]);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        let mut entries = Vec::new();
        for (pos, _) in svg.match_indices(&format!("width=\"{LEGEND_SWATCH}\"")) {
            let snippet = &svg[pos..(pos + 500).min(svg.len())];
            let fill = snippet.split("fill=\"").nth(1).unwrap().split('"').next().unwrap();
            let label = legend_label_text(snippet);
            entries.push((label.to_string(), fill.to_string()));
        }
        // Display order is still reversed(stack_order) = ["x", "y"] (stack
        // order is baseline-first: y, x — reversed puts x first), but each
        // swatch's fill follows its board slot, not its display position.
        assert_eq!(
            entries,
            vec![("x".to_string(), "#3164a3".to_string()), ("y".to_string(), "#779bc9".to_string())],
            "display order stays production's reversed(stack_order); fills key off the board slot: {svg}"
        );
    }

    #[test]
    fn horizontal_stack_zero_puts_largest_series_sum_at_origin() {
        let payload = make_payload(make_stacked_payload("horizontal", "zero"));
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("stacked horizontal renders");

        let rects = parse_rects(&svg);
        assert_eq!(rects.len(), 4, "2 categories × 2 series: {rects:?}");

        // Category "A" occupies the two segments with the smaller y (top band).
        let mut cat_a: Vec<_> = rects.iter().collect();
        cat_a.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap());
        let cat_a = &cat_a[0..2];
        let (origin, far) = if cat_a[0].0 < cat_a[1].0 { (cat_a[0], cat_a[1]) } else { (cat_a[1], cat_a[0]) };
        assert!(origin.0.abs() < 0.1, "baseline segment starts at x=0: {origin:?}");
        assert!(
            (origin.0 + origin.2 - far.0).abs() < 0.1,
            "baseline segment's right edge touches the next segment's left edge"
        );
        assert_eq!(origin.4, "#3164a3", "baseline series receives palette[0]");
        assert_eq!(far.4, "#779bc9", "outer series receives palette[1]");
    }

    /// Mirrors `vertical_stacked_board_slot_wins_over_local_baseline_first_order`
    /// for the horizontal arm's own fill lookup (bar.rs's `render_horizontal`
    /// stacked-fill call site, a separate code path from the vertical one).
    #[test]
    fn horizontal_stacked_board_slot_wins_over_local_baseline_first_order() {
        let mut payload_json = make_stacked_payload("horizontal", "zero");
        payload_json["resolved"]["category_colors"] =
            serde_json::json!([{"field": "series", "slots": {"x": 0, "y": 1}, "overrides": {}}]);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");

        let rects = parse_rects(&svg);
        assert_eq!(rects.len(), 4, "2 categories × 2 series: {rects:?}");
        let mut cat_a: Vec<_> = rects.iter().collect();
        cat_a.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap());
        let cat_a = &cat_a[0..2];
        let (origin, far) = if cat_a[0].0 < cat_a[1].0 { (cat_a[0], cat_a[1]) } else { (cat_a[1], cat_a[0]) };
        assert!(origin.0.abs() < 0.1, "baseline is still \"y\" (paint order unaffected): {origin:?}");
        assert_eq!(origin.4, "#779bc9", "baseline series (y) takes its board slot 1: {svg}");
        assert_eq!(far.4, "#3164a3", "outer series (x) takes its board slot 0: {svg}");
    }

    /// Mirrors `vertical_grouped_board_slot_wins_over_local_alphabetical_order`
    /// for the horizontal arm's own grouped-fill call site (`color_domain`
    /// keyed by enumerate index).
    #[test]
    fn horizontal_grouped_board_slot_wins_over_local_alphabetical_order() {
        let mut payload_json = make_stacked_payload("horizontal", "none");
        payload_json["resolved"]["category_colors"] =
            serde_json::json!([{"field": "series", "slots": {"x": 1, "y": 0}, "overrides": {}}]);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");

        let mut rects = parse_rects(&svg);
        assert_eq!(rects.len(), 4, "2 categories × 2 series: {rects:?}");
        // "x" is the top sub-band (si=0) within each category cluster, "y"
        // the bottom one (si=1) — sorted by y this gives xA, yA, xB, yB.
        rects.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap());
        assert_eq!(
            rects.iter().map(|r| r.4.as_str()).collect::<Vec<_>>(),
            vec!["#779bc9", "#3164a3", "#779bc9", "#3164a3"],
            "x takes its board slot 1, y its board slot 0, in every category: {svg}"
        );
    }

    /// Mirrors bar's vertical rail-ink coverage
    /// (`vertical_stacked_rail_places_series_labels_at_segment_midpoints`)
    /// for the horizontal arm's own TopRail companion-ink call site
    /// (bar.rs's `series_ink` call inside `render_horizontal`).
    #[test]
    fn horizontal_stacked_top_rail_ink_follows_the_board_slot() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("horizontal", "zero");
        payload_json["resolved"]["style"]["endpoint_labels"] =
            json!({"visible": true, "height": 20.0, "label_offset": 6.0});
        payload_json["resolved"]["style"]["series_label"] = json!({
            "font_family": TEST_FONT, "font_size": 11.0, "font_weight": "400",
            "font_style": "normal", "dark_companion_palette": ["#111111", "#222222"], "pin_companions": []
        });
        payload_json["resolved"]["category_colors"] =
            json!([{"field": "series", "slots": {"x": 0, "y": 1}, "overrides": {}}]);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        let rail = rail_label_order(&svg);
        let fills: Vec<(&str, &str)> = rail.iter().map(|(_, l, f)| (l.as_str(), f.as_str())).collect();
        // Board slots (x:0, y:1) reverse the local baseline-first order
        // (y leads, would take companion[0]) — y now wears companion[1].
        assert_eq!(fills, vec![("y", "#222222"), ("x", "#111111")], "{svg}");
    }

    /// The board's plan governs legend swatches on the horizontal STACKED
    /// arm too (mirrors `vertical_stacked_legend_display_order_ignores_the_board_slot`)
    /// — a separate `stacked_legend_entries` call site from the vertical one.
    #[test]
    fn horizontal_stacked_legend_swatches_follow_the_board_slot() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("horizontal", "zero");
        payload_json["resolved"]["legend"]["visible"] = json!(true);
        payload_json["resolved"]["category_colors"] =
            json!([{"field": "series", "slots": {"x": 0, "y": 1}, "overrides": {}}]);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        let mut entries = Vec::new();
        for (pos, _) in svg.match_indices(&format!("width=\"{LEGEND_SWATCH}\"")) {
            let snippet = &svg[pos..(pos + 500).min(svg.len())];
            let fill = snippet.split("fill=\"").nth(1).unwrap().split('"').next().unwrap();
            let label = legend_label_text(snippet);
            entries.push((label.to_string(), fill.to_string()));
        }
        assert_eq!(
            entries,
            vec![("y".to_string(), "#779bc9".to_string()), ("x".to_string(), "#3164a3".to_string())],
            "display order stays production's stack order (y, x); fills key off the board slot: {svg}"
        );
    }

    /// The board's plan governs legend swatches on the horizontal GROUPED
    /// arm too — a separate `stacked_legend_entries` call site from the
    /// stacked one above.
    #[test]
    fn horizontal_grouped_legend_swatches_follow_the_board_slot() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("horizontal", "none");
        payload_json["resolved"]["legend"]["visible"] = json!(true);
        payload_json["resolved"]["category_colors"] =
            json!([{"field": "series", "slots": {"x": 1, "y": 0}, "overrides": {}}]);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        let mut entries = Vec::new();
        for (pos, _) in svg.match_indices(&format!("width=\"{LEGEND_SWATCH}\"")) {
            let snippet = &svg[pos..(pos + 500).min(svg.len())];
            let fill = snippet.split("fill=\"").nth(1).unwrap().split('"').next().unwrap();
            let label = legend_label_text(snippet);
            entries.push((label.to_string(), fill.to_string()));
        }
        assert_eq!(
            entries,
            vec![("x".to_string(), "#779bc9".to_string()), ("y".to_string(), "#3164a3".to_string())],
            "display order stays alphabetical (x, y); fills key off the board slot: {svg}"
        );
    }

    /// The `<text>` tags matching the series-label font (11px here), as
    /// `(x, label, fill)`, in ascending-x order — the endpoint-label top rail.
    fn rail_label_order(svg: &str) -> Vec<(f64, String, String)> {
        let mut out = Vec::new();
        for (pos, _) in svg.match_indices("<text ") {
            let tag_end = svg[pos..].find("</text>").map(|i| pos + i).unwrap_or(svg.len());
            let tag = &svg[pos..tag_end];
            let is_rail_color = tag.contains("fill=\"#111111\"") || tag.contains("fill=\"#222222\"");
            if !tag.contains("text-anchor=\"middle\"") || !is_rail_color {
                continue;
            }
            let x: f64 = tag
                .split("x=\"")
                .nth(1)
                .and_then(|s| s.split('"').next())
                .and_then(|s| s.parse().ok())
                .unwrap();
            let label = tag.rsplit('>').next().unwrap().to_string();
            let fill = tag
                .split("fill=\"")
                .nth(1)
                .and_then(|s| s.split('"').next())
                .unwrap()
                .to_string();
            out.push((x, label, fill));
        }
        out.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap());
        out
    }

    #[test]
    fn horizontal_stacked_top_rail_shows_top_row_segments_in_stack_order() {
        use serde_json::json;
        // endpoint_labels.visible replaces the (already-suppressed) legend
        // with a rail above the plot, one label per series, centered on that
        // series' segment within the alphabetically-first category ("A").
        let mut payload_json = make_stacked_payload("horizontal", "zero");
        payload_json["resolved"]["style"]["endpoint_labels"] =
            json!({"visible": true, "height": 20.0, "label_offset": 6.0});
        payload_json["resolved"]["style"]["series_label"] = json!({
            "font_family": TEST_FONT, "font_size": 11.0, "font_weight": "400",
            "font_style": "normal", "dark_companion_palette": ["#111111", "#222222"], "pin_companions": []
        });
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        let rail = rail_label_order(&svg);
        assert_eq!(rail.len(), 2, "one rail label per series: {rail:?}");
        // Horizontal stack order (largest-sum-first) puts "y" (sum 45) at the
        // origin segment and "x" (sum 15) further out — the rail follows the
        // same left-to-right order as the bars themselves.
        let labels: Vec<&str> = rail.iter().map(|(_, l, _)| l.as_str()).collect();
        assert_eq!(labels, vec!["y", "x"], "rail order matches stack order: {rail:?}");
        assert!(rail[0].0 < rail[1].0, "y's segment sits left of x's: {rail:?}");
        let fills: Vec<(&str, &str)> = rail
            .iter()
            .map(|(_, label, fill)| (label.as_str(), fill.as_str()))
            .collect();
        assert_eq!(fills, vec![("y", "#111111"), ("x", "#222222")]);

        // `rail_y` is computed independently of `plot_top` (both derive from
        // `pad_t + title_h + legend_h`, plus the rail's own offset) — so a
        // regression that drops `rail_h` from `plot_top` (:582) would leave
        // `rail_y` unchanged while pulling the bars up underneath it. The
        // label-order assertions above can't catch that: this checks the
        // plot group's own translate-y sits below the rail's baseline.
        let rail_y: f64 = svg
            .match_indices("<text ")
            .map(|(pos, _)| &svg[pos..svg[pos..].find("</text>").map(|i| pos + i).unwrap_or(svg.len())])
            .find(|tag| tag.contains("fill=\"#111111\"") || tag.contains("fill=\"#222222\""))
            .and_then(|tag| tag.split("y=\"").nth(1))
            .and_then(|s| s.split('"').next())
            .and_then(|s| s.parse().ok())
            .expect("a rail <text> tag with a y attribute");
        let plot_top: f64 = svg
            .split("<g transform=\"translate(")
            .nth(1)
            .and_then(|s| s.split(',').nth(1))
            .and_then(|s| s.split(')').next())
            .and_then(|s| s.parse().ok())
            .expect("plot group has a translate(x,y)");
        assert!(
            plot_top > rail_y,
            "plot content starts below the rail's baseline (rail_h must be in plot_top): \
             plot_top={plot_top}, rail_y={rail_y}"
        );
    }

    /// The top rail is production's vconcat (`_wrap_vconcat_label_rail`):
    /// a `endpoint_labels.height`-tall pane above the chart pane, `spacing`
    /// = `label_offset`, and the chart pane placed by Vega's concat layout
    /// at the rail's bottom + spacing − the pane's own top ink — the zero
    /// rule's full stroke width (probe: 20 + 6 + 2 = 28 with a 2px rule, 26
    /// without). The rail text sits at the pane's mid-height with baseline
    /// bottom (Vega's `round(−0.21·fs)` offset → y = 10 − 2 = 8 at 11px),
    /// and its ink overhangs the pane top by `round(0.8·fs) + 2 − h/2`
    /// (1px at 11px/20px; probe-pinned across 8–20px), which the title
    /// stacks above. Both panes share the x scale, so each label is
    /// centered on its series' segment in the top category row.
    #[test]
    fn horizontal_stacked_top_rail_takes_the_concat_geometry() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("horizontal", "zero");
        payload_json["resolved"]["style"]["endpoint_labels"] =
            json!({"visible": true, "height": 20.0, "label_offset": 6.0});
        // A family/weight distinct from `axis_x.labels.font` (also
        // `TEST_FONT`/"400" in this fixture) — otherwise painting the rail
        // with `cat_family`/"400" (the pre-#8293 law) would emit the same
        // tag and the assertions below couldn't tell the two laws apart.
        payload_json["resolved"]["style"]["series_label"] = json!({
            "font_family": "'Rail Serif', sans-serif", "font_size": 14.0, "font_weight": "600",
            "font_style": "italic", "dark_companion_palette": ["#111111", "#222222"], "pin_companions": []
        });
        payload_json["resolved"]["style"]["axis_y"]["grid"] =
            json!({"visible": true, "threshold": {"color": "#8A8C8F", "width": 2.0}, "width": 1.0, "color": "#D0D3D9", "opacity": 1.0});
        // The headroomed stacked max production bakes (40 · 1.05), which the
        // shared scale nices to 45.
        payload_json["resolved"]["stacked_domain_max"] = json!(42.0);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");

        let rail = rail_label_order(&svg);
        assert_eq!(rail.len(), 2, "one rail label per series: {rail:?}");
        let rail_tag = |fill: &str| -> String {
            let pos = svg.find(&format!("fill=\"{fill}\"")).unwrap();
            let start = svg[..pos].rfind("<text").unwrap();
            svg[start..pos].to_string()
        };
        let attr = |tag: &str, name: &str| -> f64 {
            tag.split(&format!(" {name}=\"")).nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        // pad_t 16 + title block 28 (18px + offset 10) + 4px rail-text
        // overhang at 14px = rail top 48; text at 48 + 7.
        let tag = rail_tag("#111111");
        assert_eq!(attr(&tag, "y"), 55.0, "rail label y: {tag}");
        // The rail's mark carries series_label's own font props — production
        // stamps them onto the vconcat text mark (translate.py's
        // `_wrap_vconcat_label_rail`), same as the vertical rail's pane.
        assert_eq!(attr(&tag, "font-size"), 14.0, "rail label size: {tag}");
        assert!(tag.contains("font-family=\"'Rail Serif', sans-serif\""), "rail label family: {tag}");
        assert!(tag.contains("font-weight=\"600\""), "rail label weight: {tag}");
        assert!(tag.contains("font-style=\"italic\""), "rail label style: {tag}");
        let (plot_left, plot_top): (f64, f64) = {
            let t = svg.split("<g transform=\"translate(").nth(1).unwrap();
            let mut it = t.split(')').next().unwrap().split(',');
            (it.next().unwrap().parse().unwrap(), it.next().unwrap().parse().unwrap())
        };
        // rail top 48 + rail 20 + spacing 6 + zero-rule stroke 2.
        assert_eq!(plot_top, 76.0, "chart pane top");
        // Shared x scale: each label centered on its own segment in the top
        // row (category A: y spans 0..30, x spans 30..40).
        let rects = parse_rects(&svg);
        let top_row_y = rects.iter().map(|r| r.1).fold(f64::INFINITY, f64::min);
        for (label_x, label, _) in &rail {
            let fill = if label == "y" { "#3164a3" } else { "#779bc9" };
            let seg = rects
                .iter()
                .find(|r| (r.1 - top_row_y).abs() < 0.01 && r.4 == fill)
                .unwrap_or_else(|| panic!("no top-row segment for {label}: {rects:?}"));
            let mid = plot_left + seg.0 + seg.2 / 2.0;
            assert!((label_x - mid).abs() < 0.05, "{label} label at {label_x}, segment mid {mid}");
        }
        // The shared scale is niced to 45: the baked ladder's 50 rung is
        // dropped, the 25 rung stays.
        assert!(svg.contains(">25</text>") && !svg.contains(">50</text>"), "niced ladder: {svg}");
    }

    /// Which value labels a rendered SVG draws, in tick order.
    fn drawn_value_labels(svg: &str, candidates: &[&str]) -> Vec<String> {
        candidates.iter().filter(|l| svg.contains(&format!(">{l}</text>"))).map(|l| l.to_string()).collect()
    }

    /// Vega's `labelOverlap` parity transform (vega-encode overlap.js) on a
    /// value axis under the theme's `labels.min_gap` (`labelSeparation`):
    /// while three or more labels remain and any adjacent pair's ink sits
    /// closer than the separation, every other survivor is hidden.
    /// Fourteen rungs 0–650 by 50 on a ~330px plot pitch 25px against
    /// ~20px labels, so the odd rungs go and the even ones (0–600 by 100)
    /// clear 16px at a 51px pitch — the `series-label-placement` stacked
    /// references' seven of fourteen.
    #[test]
    fn dense_value_labels_take_vega_parity_under_min_gap() {
        use serde_json::json;
        let ladder: Vec<f64> = (0..14).map(|i| i as f64 * 50.0).collect();
        for orientation in ["horizontal", "vertical"] {
            let mut payload_json = make_stacked_payload(orientation, "zero");
            payload_json["resolved"]["style"]["axis_y"]["tick_values"] = json!(ladder);
            payload_json["resolved"]["style"]["axis_y"]["labels"]["min_gap"] = json!(16.0);
            payload_json["resolved"]["stacked_domain_max"] = json!(650.0);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            let drawn = drawn_value_labels(&svg, &["0", "50", "100", "150", "550", "600", "650"]);
            assert_eq!(drawn, ["0", "100", "600"], "{orientation}: every other label hidden: {svg}");
            if orientation == "vertical" {
                // Only the visible labels' ink reaches the fit: the hidden
                // "650" at the plot top would otherwise reserve its
                // overhang above the plot (pad 16 + the 18px title block
                // 28 = 44, not 50).
                let plot_top: f64 = {
                    let t = svg.split("<g transform=\"translate(").nth(1).unwrap();
                    t.split(')').next().unwrap().split(',').nth(1).unwrap().parse().unwrap()
                };
                assert_eq!(plot_top, 44.0, "no reservation for hidden label ink: {svg}");
            }
        }
    }

    /// The overlap test reads the labels' placed ink: a last label flush
    /// at the range end sits nearer its neighbor than a centered one
    /// would, and that pair alone can trip the parity. Seven rungs at a
    /// 55px pitch clear a 30px separation everywhere except between the
    /// centered "500" and the flush "600" (gap ≈ 24), so parity fires once
    /// — the `quick-guide_7` references' every-fourth label.
    #[test]
    fn flush_end_label_counts_with_its_flush_ink_in_the_parity_test() {
        use serde_json::json;
        let ladder: Vec<f64> = (0..7).map(|i| i as f64 * 100.0).collect();
        let mut payload_json = make_stacked_payload("horizontal", "zero");
        payload_json["resolved"]["style"]["axis_y"]["tick_values"] = json!(ladder);
        payload_json["resolved"]["style"]["axis_y"]["labels"]["min_gap"] = json!(30.0);
        payload_json["resolved"]["stacked_domain_max"] = json!(600.0);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        let drawn = drawn_value_labels(&svg, &["0", "100", "200", "300", "400", "500", "600"]);
        assert_eq!(drawn, ["0", "200", "400", "600"], "parity from the flush pair alone: {svg}");
    }

    /// With fewer than three survivors and the axis's last label hidden,
    /// Vega shows the last label in place of the last survivor: four
    /// labels that all collide leave 0 and 600, not 0 and 400.
    #[test]
    fn parity_keeps_the_axis_end_label_when_fewer_than_three_survive() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("horizontal", "zero");
        payload_json["resolved"]["style"]["axis_y"]["tick_values"] = json!([0.0, 200.0, 400.0, 600.0]);
        payload_json["resolved"]["style"]["axis_y"]["labels"]["min_gap"] = json!(200.0);
        payload_json["resolved"]["stacked_domain_max"] = json!(600.0);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        let drawn = drawn_value_labels(&svg, &["0", "200", "400", "600"]);
        assert_eq!(drawn, ["0", "600"], "the end label survives: {svg}");
    }

    /// Eighteen "Category NN" labels on the reference's 564×408 canvas
    /// (`time-unit-label-cadence-matrix/categories_tilt`): x ticks hidden,
    /// value axis on the right so the left margin is the labels' own
    /// overhang, square bars.
    fn make_tilt_payload(angle: Option<f64>) -> serde_json::Value {
        use serde_json::json;
        let cats: Vec<(String, f64)> =
            (1..=18).map(|i| (format!("Category {i:02}"), 50.0 + i as f64)).collect();
        let refs: Vec<(&str, f64)> = cats.iter().map(|(c, v)| (c.as_str(), *v)).collect();
        let mut p = make_vertical_payload(&refs, false);
        p["width"] = json!(564.0);
        p["height"] = json!(408.0);
        p["resolved"]["style"]["label_usable_ratio"] = json!(0.8);
        p["resolved"]["style"]["mark"]["border"]["radius"] = json!(0.0);
        p["resolved"]["style"]["axis_y"]["position"] = json!("right");
        p["resolved"]["style"]["axis_y"]["ticks"]["visible"] = json!(false);
        let ax = &mut p["resolved"]["style"]["axis_x"];
        ax["ticks"]["visible"] = json!(false);
        ax["labels"]["overlap"] = json!({"skip": true, "tilt": true});
        ax["labels"]["tilt_increments"] = json!([0.0, -30.0, -45.0, -60.0, -90.0]);
        ax["labels"]["angle"] = json!(angle);
        p
    }

    /// `(rotate, dy, anchor, translate x, translate y)` of the first
    /// category label's rotated text, or None when it draws flat.
    fn rotated_label(svg: &str, text: &str) -> Option<(f64, f64, String, f64, f64)> {
        let tag = svg.split('<').find(|t| t.ends_with(&format!(">{text}")))?;
        let tf = tag.split("transform=\"").nth(1)?.split('"').next()?;
        let mut nums = tf
            .split(|c: char| !(c.is_ascii_digit() || c == '.' || c == '-'))
            .filter(|s| !s.is_empty())
            .map(|s| s.parse::<f64>().unwrap());
        let (tx, ty, rot, _dx, dy) =
            (nums.next()?, nums.next()?, nums.next()?, nums.next()?, nums.next()?);
        let anchor = tag.split("text-anchor=\"").nth(1)?.split('"').next()?.to_string();
        Some((rot, dy, anchor, tx, ty))
    }

    fn plot_left(svg: &str) -> f64 {
        let t = svg.split("<g transform=\"translate(").nth(1).unwrap();
        t.split(')').next().unwrap().split(',').next().unwrap().parse().unwrap()
    }

    /// Production's ordinal branch (`resolve_axis_x_overlap`): labels that
    /// don't fit flat walk the theme's tilt ladder to the first angle whose
    /// rotated footprint (`w·cos + fs·sin`) fits one band of the usable
    /// width; Vega-Lite then rotates the labels (anchor right, baseline
    /// middle past 45°, top below) and Vega's layout reserves the rotated
    /// text box. Probe-pinned (vl-convert, Inter registered, 564×408,
    /// padding 16, y axis hidden): the bottom gutter is
    /// `ceil(padding + rotated reach)` — 72/65/60/49 for −90/−60/−45/−30
    /// over these eighteen labels — and the first label's left overhang
    /// is `ceil(reach − label x + 0.5)` (41/31/23 at the probe's own band
    /// centers, the flat labels' ±0.5 convention).
    #[test]
    fn crowded_category_labels_take_the_tilt_ladder_and_vega_s_rotated_bounds() {
        let fonts = test_fonts();
        let svg = render(&make_payload(make_tilt_payload(None)), &fonts).expect("renders");
        let (rot, dy, anchor, tx, ty) = rotated_label(&svg, "Category 01").expect("rotated");
        assert_eq!((rot, dy, anchor.as_str()), (270.0, 3.0, "end"), "{svg}");
        // No label overhang at −90: only the 2px zero rule's stroke holds
        // the plot off the padding edge (the reference's origin x 18).
        assert_eq!(plot_left(&svg), 18.0, "{svg}");
        assert!((tx - (18.0 + 495.0 / 36.0)).abs() < 0.02, "band center: {tx}");
        // Anchor y = plot bottom + 0.5 (crisp) + labelPadding 6; canvas
        // bottom edge 392 − plot bottom = the 72px gutter.
        assert_eq!(392.0 - (ty - 6.5), 72.0, "{svg}");

        let w = fonts.measure_shaped("Category 01", TEST_FONT, 11.0).unwrap();
        for (angle, gutter, anchor, dy) in [
            (-60.0, 65.0, "end", 3.0),
            (-45.0, 60.0, "end", 9.0),
            (-30.0, 49.0, "end", 9.0),
        ] {
            let svg = render(&make_payload(make_tilt_payload(Some(angle))), &fonts).expect("renders");
            let (rot, got_dy, got_anchor, tx, ty) = rotated_label(&svg, "Category 01").expect("rotated");
            assert_eq!((rot, got_dy, got_anchor.as_str()), (360.0 + angle, dy, anchor), "{angle}: {svg}");
            assert_eq!(392.0 - (ty - 6.5), gutter, "{angle} gutter: {svg}");
            // The rotated box's far corner: the baseline end at −w, and
            // for a middle baseline the box top 6px above the anchor.
            let (sin, cos) = (angle as f64).abs().to_radians().sin_cos();
            let reach = w * cos + if dy == 3.0 { 6.0 * sin } else { 0.0 };
            let left = plot_left(&svg);
            assert_eq!(left - 16.0, (reach - (tx - left) + 0.5).ceil(), "{angle} left overhang: {svg}");
        }
    }

    /// The tilted category axis's title is Vega's `axisTitleLayout`: it sits
    /// `labelPadding`-anchored at the pivot plus the label column's rotated
    /// reach (the SAME `rotated_category_label_ink` the margin reserves with,
    /// at the pivot, maxed over every label) plus `title.padding` plus the
    /// "top" baseline shift. Pinned from that arithmetic against the rotated
    /// labels' own anchor y, never a value read off the title.
    #[test]
    fn tilted_category_axis_title_sits_below_the_rotated_label_column() {
        use serde_json::json;
        let fonts = test_fonts();
        let widest = (1..=18)
            .map(|i| fonts.measure_shaped(&format!("Category {i:02}"), TEST_FONT, 11.0).unwrap())
            .fold(0.0_f64, f64::max);
        let (title_padding, title_fs) = (10.0, 11.0);
        // (authored angle, drawn anchor/baseline) — `None` walks the crowding
        // ladder to -90.
        for angle in [None, Some(-90.0), Some(-45.0)] {
            let mut p = make_tilt_payload(angle);
            p["resolved"]["style"]["axis_x"]["title"] =
                json!({"font": {"family": TEST_FONT, "size": title_fs, "weight": "600"}, "padding": title_padding, "visible": true});
            let svg = render(&make_payload(p), &fonts).expect("renders");
            let (rot, dy, anchor, _tx, anchor_y) = rotated_label(&svg, "Category 01").expect("rotated");
            let (align, baseline) = match anchor.as_str() {
                "end" => ("right", if dy == 3.0 { "middle" } else { "top" }),
                other => panic!("unexpected anchor {other}: {svg}"),
            };
            // Reach of the widest label, rotated about the pivot.
            let reach = vega_fit::text_ink(0.0, 0.0, widest, 11.0, align, baseline)
                .rotate(rot, 0.0, 0.0)
                .y2;
            let expected = anchor_y + reach + title_padding + measure::baseline_shift("top", title_fs);
            let title_tag = {
                let end = svg.find(">Product<").expect("title drawn");
                let start = svg[..end].rfind("<text").unwrap();
                &svg[start..end]
            };
            let title_y: f64 =
                title_tag.split("y=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
            assert!((title_y - expected).abs() < 0.01, "{angle:?}: title y {title_y} vs {expected} in {svg}");
        }
    }

    /// A rotated category label's reach is its width as Vega measures it,
    /// kerning applied (`measure_shaped`), not the raw advance:
    /// `docs/laws-shared.md#which-measurer-a-width-takes-shaped-vs-raw`. At
    /// the authored −90° the bottom gutter is `ceil(labelPadding(6) + widest
    /// label)`; "To Tow Try" is 60.0059 raw but 57.6372 shaped, so the
    /// gutter is 64 where the raw advance measures 67.
    #[test]
    fn rotated_category_label_reach_takes_the_shaped_width() {
        use serde_json::json;
        let fonts = test_fonts();
        let labels = ["To Tow Try", "AV Travel", "LAVA Tray"];
        let widest = labels
            .iter()
            .map(|l| fonts.measure_shaped(l, TEST_FONT, 11.0).unwrap())
            .fold(0.0_f64, f64::max);
        assert_eq!((6.0 + widest).ceil(), 64.0, "fixture: shaped and raw gutters differ");
        let mut p = make_tilt_payload(Some(-90.0));
        let rows: Vec<serde_json::Value> = labels
            .iter()
            .map(|l| json!({"product": l, "revenue": 60.0}))
            .collect();
        p["rows"] = json!(rows);
        let svg = render(&make_payload(p), &fonts).expect("renders");
        let (_, _, _, _, ty) = rotated_label(&svg, "To Tow Try").expect("rotated");
        assert_eq!(392.0 - (ty - 6.5), 64.0, "{svg}");
    }

    /// Production's `_max_pair` tests adjacent pairs' mean advance, never
    /// a lone wide label: "Northern Territory" beside five one-letter
    /// categories at 600px averages well under the band, so nothing tilts.
    #[test]
    fn crowding_reads_the_widest_adjacent_pair_not_the_widest_label() {
        use serde_json::json;
        let cats = [("Northern Territory", 100.0), ("B", 100.0), ("C", 100.0), ("D", 100.0), ("E", 100.0), ("F", 100.0)];
        let mut p = make_vertical_payload(&cats, false);
        p["width"] = json!(600.0);
        p["resolved"]["style"]["label_usable_ratio"] = json!(0.8);
        p["resolved"]["style"]["axis_x"]["labels"]["overlap"] = json!({"skip": true, "tilt": true});
        p["resolved"]["style"]["axis_x"]["labels"]["tilt_increments"] = json!([0.0, -30.0, -45.0, -60.0, -90.0]);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(rotated_label(&svg, "Northern Territory").is_none(), "flat: {svg}");
        assert!(svg.contains("text-anchor=\"middle\" font-family=\"'Inter Variable', Inter, sans-serif\" font-size=\"11\" font-weight=\"400\" fill=\"#626366\">Northern Territory</text>"), "drawn flat and centered: {svg}");
    }

    /// A temporal axis takes production's `_temporal_layout`, whose tilt
    /// is not modeled here: thirteen crowded same-weekday dates 28 days
    /// apart (no calendar grain detects, so no cadence labels either) draw
    /// flat, as production draws them.
    #[test]
    fn temporal_category_labels_never_take_the_ordinal_ladder() {
        let _locale = crate::time_locale::en_us_scope();
        use serde_json::json;
        let dates = [
            "2025-01-06", "2025-02-03", "2025-03-03", "2025-03-31", "2025-04-28", "2025-05-26",
            "2025-06-23", "2025-07-21", "2025-08-18", "2025-09-15", "2025-10-13", "2025-11-10",
            "2025-12-08",
        ];
        let refs: Vec<(&str, f64)> = dates.iter().map(|d| (*d, 100.0)).collect();
        let mut p = make_vertical_payload(&refs, false);
        p["width"] = json!(300.0);
        p["resolved"]["style"]["label_usable_ratio"] = json!(0.8);
        p["resolved"]["style"]["axis_x"]["labels"]["overlap"] = json!({"skip": true, "tilt": true});
        p["resolved"]["style"]["axis_x"]["labels"]["tilt_increments"] = json!([0.0, -30.0, -45.0, -60.0, -90.0]);
        let svg = render(&make_payload(p.clone()), &test_fonts()).expect("renders");
        assert!(!svg.contains("rotate("), "temporal labels stay flat: {svg}");
        // An authored angle wins before the type routing, on a date axis too.
        p["resolved"]["style"]["axis_x"]["labels"]["angle"] = json!(-45.0);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(svg.contains("rotate(-45)"), "authored angle drawn: {svg}");
    }

    /// An authored angle on a calendar-grain axis skips only the thinning and
    /// tick restoration: every bucket still formats at the data grain and
    /// Vega rotates the two-row text as one block, the context row riding the
    /// anchor's block.
    #[test]
    fn an_authored_angle_on_a_calendar_grain_axis_rotates_every_bucket_as_one_block() {
        use serde_json::json;
        let weekly = [
            "2025-01-06", "2025-01-13", "2025-01-20", "2025-01-27", "2025-02-03", "2025-02-10",
            "2025-02-17", "2025-02-24", "2025-03-03", "2025-03-10", "2025-03-17", "2025-03-24",
            "2025-03-31",
        ];
        let refs: Vec<(&str, f64)> = weekly.iter().map(|d| (*d, 100.0)).collect();
        let mut p = make_vertical_payload(&refs, false);
        p["width"] = json!(300.0);
        p["resolved"]["style"]["axis_x"]["labels"]["angle"] = json!(-45.0);
        let svg = render(&make_payload(p.clone()), &test_fonts()).expect("renders");
        assert_eq!(svg.matches("rotate(315) translate(0,").count(), weekly.len(), "{svg}");
        assert!(svg.contains("<tspan>6</tspan><tspan x=\"0\" dy=\"13\">Jan'25</tspan>"), "{svg}");
        // A steep angle flows the context row inline on one row.
        p["resolved"]["style"]["axis_x"]["labels"]["angle"] = json!(-90.0);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(svg.contains(">6 Jan'25<") && !svg.contains("<tspan"), "{svg}");
    }

    /// The ladder's footprint is `w·cos + fs·sin`: twelve "Category NN"
    /// labels on 564px (band 37.6) fail −60 only through the `fs·sin`
    /// term (32.75 + 9.53 > 37.6) and land on −90.
    #[test]
    fn the_ladder_footprint_counts_the_rotated_font_height() {
        use serde_json::json;
        let mut p = make_tilt_payload(None);
        let rows: Vec<serde_json::Value> =
            (1..=12).map(|i| json!({"product": format!("Category {i:02}"), "revenue": 60.0})).collect();
        p["rows"] = json!(rows);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let (rot, _, _, _, _) = rotated_label(&svg, "Category 01").expect("rotated");
        assert_eq!(rot, 270.0, "{svg}");
    }

    /// Four short labels clear the flat-fit test, so the ladder never
    /// runs: flat, centered, top-baseline labels as before.
    #[test]
    fn category_labels_that_fit_flat_stay_flat() {
        use serde_json::json;
        let mut p = make_tilt_payload(None);
        p["rows"] = json!([
            {"product": "A", "revenue": 51.0}, {"product": "B", "revenue": 52.0},
            {"product": "C", "revenue": 53.0}, {"product": "D", "revenue": 54.0}
        ]);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(rotated_label(&svg, "A").is_none(), "{svg}");
        assert!(svg.contains("text-anchor=\"middle\""), "{svg}");
    }

    /// `temporal-label-cadence-transitions/wk_irregular`: sixteen weekly
    /// rows over a twenty-week span. Production's ordinal scaffold gives
    /// every week a band — the four missing ones empty — so the bar for
    /// 2025-02-10 (the sixth week) sits five band steps past the first,
    /// not three.
    #[test]
    fn missing_calendar_buckets_become_empty_bands() {
        let weeks = [
            "2025-01-06", "2025-01-13", "2025-01-20", "2025-02-10", "2025-02-17", "2025-02-24",
            "2025-03-03", "2025-03-10", "2025-03-17", "2025-03-31", "2025-04-07", "2025-04-14",
            "2025-04-28", "2025-05-05", "2025-05-12", "2025-05-19",
        ];
        let cats: Vec<(String, f64)> =
            weeks.iter().map(|w| (format!("{w}T00:00:00"), 100.0)).collect();
        let refs: Vec<(&str, f64)> = cats.iter().map(|(c, v)| (c.as_str(), *v)).collect();
        let mut p = make_vertical_payload(&refs, false);
        p["resolved"]["style"]["axis_x"]["fill"] = serde_json::json!("null");
        p["resolved"]["style"]["mark"]["border"]["radius"] = serde_json::json!(0.0);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let xs: Vec<f64> = svg
            .match_indices("<rect x=\"")
            .map(|(i, _)| svg[i + 9..].split('"').next().unwrap().parse().unwrap())
            .collect();
        assert_eq!(xs.len(), 16, "one bar per row: {svg}");
        let step = xs[1] - xs[0];
        assert!((xs[3] - xs[0] - 5.0 * step).abs() < 0.02, "2025-02-10 at band 5: {xs:?}");
        assert!((xs[15] - xs[0] - 19.0 * step).abs() < 0.02, "2025-05-19 at band 19: {xs:?}");
    }

    /// A normalized stack over a gapped weekly grain: the synthesized week
    /// has no rows, so no category total — an empty column, not an error.
    #[test]
    fn a_synthesized_bucket_is_an_empty_column_under_normalize() {
        use serde_json::json;
        let mut p = make_stacked_payload("vertical", "normalize");
        p["rows"] = json!([
            {"cat": "2025-01-06", "series": "x", "val": 10.0},
            {"cat": "2025-01-06", "series": "y", "val": 30.0},
            {"cat": "2025-01-13", "series": "x", "val": 5.0},
            {"cat": "2025-01-13", "series": "y", "val": 15.0},
            {"cat": "2025-01-27", "series": "x", "val": 8.0},
            {"cat": "2025-01-27", "series": "y", "val": 8.0}
        ]);
        p["resolved"]["style"]["axis_x"]["fill"] = json!("null");
        let svg = render(&make_payload(p), &test_fonts()).expect("a gapped normalized stack renders");
        let xs: Vec<f64> = svg
            .match_indices("<rect x=\"")
            .map(|(i, _)| svg[i + 9..].split('"').next().unwrap().parse().unwrap())
            .collect();
        assert_eq!(xs.len(), 6, "two segments per present week: {svg}");
        let step = xs[2] - xs[0];
        assert!((xs[4] - xs[0] - 3.0 * step).abs() < 0.02, "the third week sits past the empty one: {xs:?}");
    }

    /// The gate's three branches, on the same daily rows. Three dailies a
    /// year apart synthesize 363 empty buckets, past `max(3, 60)`:
    /// production's axis resolves continuous-temporal there, so each bar sits
    /// at its instant (the third 365 days past the first). A non-null
    /// `fill` or an authored grain scaffolds regardless — 366 bands, the
    /// third bar 365 steps out. Sixty-one monthly rows with one gap are
    /// 62 coarse buckets, past the 60-bucket cap: a time axis too.
    #[test]
    fn the_scaffold_gate_follows_production_s_budget_fill_and_authored_grain_branches() {
        let _locale = crate::time_locale::en_us_scope();
        use serde_json::json;
        let bar_xs = |p: serde_json::Value| -> Vec<f64> {
            let svg = render(&make_payload(p), &test_fonts()).expect("renders");
            svg.match_indices("<rect x=\"")
                .map(|(i, _)| svg[i + 9..].split('"').next().unwrap().parse().unwrap())
                .collect()
        };
        let daily = |fill: &str, time_unit: Option<&str>| {
            let mut p = make_vertical_payload(
                &[("2025-01-01", 500.0), ("2025-01-02", 1000.0), ("2026-01-01", 1500.0)],
                false,
            );
            p["resolved"]["style"]["mark"]["border"]["radius"] = json!(0.0);
            p["resolved"]["style"]["label_usable_ratio"] = json!(0.8);
            p["resolved"]["style"]["axis_x"]["fill"] = json!(fill);
            p["resolved"]["style"]["axis_x"]["time_unit"] = json!(time_unit);
            p
        };
        let xs = bar_xs(daily("null", None));
        let step = xs[1] - xs[0];
        assert!((xs[2] - xs[0] - 365.0 * step).abs() < 0.01 * 365.0, "past-budget dailies sit at their instants: {xs:?}");
        for p in [daily("zero", None), daily("null", Some("yearmonthdate"))] {
            let xs = bar_xs(p);
            let step = xs[1] - xs[0];
            assert!(((xs[2] - xs[0]) / step - 365.0).abs() < 1.0, "scaffolded to 366 bands: {xs:?}");
        }
        let months: Vec<(String, f64)> = (0..62)
            .filter(|i| *i != 30)
            .map(|i| (format!("{}-{:02}-01", 2020 + i / 12, i % 12 + 1), 1000.0))
            .collect();
        let refs: Vec<(&str, f64)> = months.iter().map(|(c, v)| (c.as_str(), *v)).collect();
        let mut p = make_vertical_payload(&refs, false);
        p["resolved"]["style"]["mark"]["border"]["radius"] = json!(0.0);
        p["resolved"]["style"]["label_usable_ratio"] = json!(0.8);
        p["resolved"]["style"]["axis_x"]["fill"] = json!("null");
        let xs = bar_xs(p);
        let step = xs[1] - xs[0];
        assert!(xs[60] - xs[0] > 61.0 * step, "62 coarse buckets sit at their dates, not on 60 equal steps: {xs:?}");
    }

    /// Production bakes the measured flip gutter into `labelPadding` only
    /// when `labels.align` is the axis's own side (`emitters/bar.py`:
    /// `cat_align == cat_orient`). Without one the labels take Vega-Lite's
    /// default: right-aligned at −labelPadding, the gutter the padding plus
    /// the measured width — probe: margins 23 (Q1–Q4, 15.52px) and 69
    /// (Onboarding, 61.13px) at padding 6 and offset 1 against the flip
    /// model's 21; `series-label-placement` and `quick-guide_7` sit 2px
    /// further right than the flip model on every chart.
    #[test]
    fn category_labels_without_an_own_side_align_take_the_end_anchored_gutter() {
        use serde_json::json;
        let fonts = test_fonts();
        let w = fonts.measure("Alpha", TEST_FONT, 11.0).unwrap().max(fonts.measure("Beta", TEST_FONT, 11.0).unwrap());
        let render_with = |align: serde_json::Value| -> (f64, String) {
            let mut p = make_horizontal_payload();
            p["resolved"]["style"]["axis_x"]["labels"]["align"] = align;
            let svg = render(&make_payload(p), &fonts).expect("renders");
            let plot_left: f64 = svg
                .split("<g transform=\"translate(")
                .nth(1)
                .and_then(|s| s.split(',').next())
                .and_then(|s| s.parse().ok())
                .unwrap();
            let pos = svg.find(">Alpha</text>").unwrap();
            (plot_left, svg[svg[..pos].rfind("<text").unwrap()..pos].to_string())
        };
        let (flip_left, flip_tag) = render_with(json!("left"));
        assert_eq!(flip_left, 16.0 + (w + 4.0).ceil(), "own-side align keeps the measured flip gutter");
        assert!(flip_tag.contains("text-anchor=\"start\""), "{flip_tag}");
        let (left, tag) = render_with(json!(null));
        assert_eq!(left, 16.0 + (6.0 + w).ceil(), "padding + measured width: {tag}");
        assert!(tag.contains("text-anchor=\"end\""), "end-anchored: {tag}");
        assert!(tag.contains(&format!(" x=\"{:.2}\"", left - 6.0 + 0.5)), "at −labelPadding in the crisp group: {tag}");
    }

    /// Bars of a rendered SVG as `(pos, size)` along the category axis:
    /// `(y, height)` for horizontal bars, `(x, width)` for vertical ones.
    fn bar_bands(svg: &str, horizontal: bool) -> Vec<(f64, f64)> {
        let mut v: Vec<(f64, f64)> = parse_rects(svg)
            .iter()
            .filter(|r| r.2 != 10.0 && r.3 != 10.0)
            .map(|r| if horizontal { (r.1, r.3) } else { (r.0, r.2) })
            .collect();
        v.sort_by(|a, b| a.partial_cmp(b).unwrap());
        v.dedup();
        v
    }

    /// Production sizes a grouped bar's sub-band mark from its overlap
    /// fraction, not the theme's band_width: `height: {band: 0.9}` for any
    /// series count but two (`_resolve_overlap_fraction`'s −0.1), and
    /// Vega-Lite offsets the mark by `band: 0.05` of the CATEGORY scale —
    /// 0.05 × the outer bandwidth, not of the sub-step. Probe: three
    /// series in a 300px range at paddings 0.2/0.2 (step 93.75, bandwidth
    /// 75, sub-step 25) draw 22.5px bars at 22.5/47.5/72.5 = 18.75 + 3.75
    /// + 25·i; four series draw 16.875px bars at 22.5/41.25/…; the
    /// `quick-guide_7` references' rows.
    #[test]
    fn grouped_bars_take_the_overlap_fraction_and_the_category_band_offset() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("horizontal", "none");
        payload_json["rows"] = json!([
            {"cat": "A", "series": "x", "val": 10.0}, {"cat": "A", "series": "y", "val": 30.0}, {"cat": "A", "series": "z", "val": 20.0},
            {"cat": "B", "series": "x", "val": 5.0}, {"cat": "B", "series": "y", "val": 15.0}, {"cat": "B", "series": "z", "val": 25.0}
        ]);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        let bands = bar_bands(&svg, true);
        assert_eq!(bands.len(), 6, "{bands:?}");
        let sub = bands[1].0 - bands[0].0;
        let step = bands[3].0 - bands[0].0;
        assert!((sub - 0.8 * step / 3.0).abs() < 0.02, "sub-step = bandwidth / 3: {bands:?}");
        for (_, h) in &bands {
            assert!((h - 0.9 * sub).abs() < 0.02, "0.9 × sub-step: {bands:?}");
        }
        // Band start 0.2·step, plus 0.05 of the 0.8·step bandwidth.
        assert!((bands[0].0 - 0.24 * step).abs() < 0.02, "first bar at 0.24·step: {bands:?}");
    }

    /// Exactly two series take production's partial overlap (+0.25): a
    /// horizontal bar is `1.25 × bandwidth('yOffset')` tall and centered on
    /// its sub-band (probe: 46.875px bars at 14.06/51.56 in a 37.5px
    /// sub-step); a vertical bar is `width: {band: 1.25}`, positioned by
    /// the same category-band offset, `(1 − 1.25)/2` of the outer
    /// bandwidth (probe: 46.875px columns at 9.375/46.875).
    #[test]
    fn two_series_take_the_partial_overlap() {
        let svg = render(&make_payload(make_stacked_payload("horizontal", "none")), &test_fonts()).expect("renders");
        let bands = bar_bands(&svg, true);
        assert_eq!(bands.len(), 4, "{bands:?}");
        let sub = bands[1].0 - bands[0].0;
        let step = bands[2].0 - bands[0].0;
        assert!((sub - 0.4 * step).abs() < 0.02, "{bands:?}");
        assert!((bands[0].1 - 1.25 * sub).abs() < 0.02, "1.25 × sub-step: {bands:?}");
        assert!((bands[0].0 - (0.2 * step - 0.125 * sub)).abs() < 0.02, "centered on the sub-band: {bands:?}");

        let mut p = make_vertical_payload(&[("Alpha", 1000.0), ("Beta", 2000.0)], true);
        p["resolved"]["style"]["mark"]["border"]["radius"] = serde_json::json!(0.0);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let bands = bar_bands(&svg, false);
        assert_eq!(bands.len(), 4, "{bands:?}");
        let sub = bands[1].0 - bands[0].0;
        let step = bands[2].0 - bands[0].0;
        assert!((bands[0].1 - 1.25 * sub).abs() < 0.02, "width band 1.25: {bands:?}");
        assert!((bands[0].0 - (0.2 * step - 0.125 * 0.8 * step)).abs() < 0.02, "category-band offset: {bands:?}");
    }

    /// Rows of a two-series grouped bar over both signs: category A's
    /// first series rises and its second falls, category B's the reverse.
    fn mixed_sign_grouped_rows() -> serde_json::Value {
        serde_json::json!([
            {"cat": "A", "series": "x", "val": 10.0}, {"cat": "A", "series": "y", "val": -20.0},
            {"cat": "B", "series": "x", "val": -15.0}, {"cat": "B", "series": "y", "val": 25.0}
        ])
    }

    /// A mixed-sign measure splits each production bar into a positive and
    /// a negative sub-layer, and since `bar_mark_props` reaches every bar
    /// sub-layer (`emitters/bar.py:314`) both take the authored overlap's
    /// band: the same `1.25 × sub-step` columns at the same category-band
    /// offset as an all-positive group, standing on the zero rule above it
    /// (the positive layer, painted first) or hanging from it (the negative
    /// layer). Probe: `bar-mixed-sign-overlap/07`, 90.99px columns in a
    /// 72.79px sub-step at `0.2·step − 0.125·0.8·step`.
    #[test]
    fn mixed_sign_vertical_group_takes_the_overlap_on_both_sign_sub_layers() {
        use serde_json::json;
        let mut p = make_stacked_payload("vertical", "none");
        p["rows"] = mixed_sign_grouped_rows();
        let axis_y = &mut p["resolved"]["style"]["axis_y"];
        axis_y["tick_values"] = json!([-30.0, -15.0, 0.0, 15.0, 30.0]);
        axis_y["domain_max"] = json!(30.0);
        axis_y["grid"]["visible"] = json!(true);
        axis_y["grid"]["threshold"] = json!({"color": "#8A8C8F", "width": 2.0});
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let bands = bar_bands(&svg, false);
        assert_eq!(bands.len(), 4, "{bands:?}");
        let sub = bands[1].0 - bands[0].0;
        let step = bands[2].0 - bands[0].0;
        for (_, w) in &bands {
            assert!((w - 1.25 * sub).abs() < 0.02, "both signs take width band 1.25: {bands:?}");
        }
        assert!((bands[0].0 - (0.2 * step - 0.125 * 0.8 * step)).abs() < 0.02, "category-band offset: {bands:?}");
        let zero_y = zero_rule_y(&svg);
        let rects: Vec<_> = parse_rects(&svg).into_iter().filter(|r| r.2 != 10.0 && r.3 != 10.0).collect();
        assert_eq!(rects.len(), 4, "{rects:?}");
        let on_the_rule = |r: &(f64, f64, f64, f64, String)| (r.1 + r.3 - zero_y).abs() < 0.1;
        let from_the_rule = |r: &(f64, f64, f64, f64, String)| (r.1 - zero_y).abs() < 0.1;
        assert!(rects[..2].iter().all(on_the_rule), "the positive layer paints first: {rects:?} / {zero_y}");
        assert!(rects[2..].iter().all(from_the_rule), "then the negative layer: {rects:?} / {zero_y}");
    }

    /// The horizontal arm: `size: 1.25 × bandwidth('yOffset')` reaches both
    /// sign sub-layers, the negative bars hanging left of the zero rule.
    #[test]
    fn mixed_sign_horizontal_group_takes_the_overlap_on_both_sign_sub_layers() {
        use serde_json::json;
        let mut p = make_stacked_payload("horizontal", "none");
        p["rows"] = mixed_sign_grouped_rows();
        let axis_y = &mut p["resolved"]["style"]["axis_y"];
        axis_y["tick_values"] = json!([-30.0, -15.0, 0.0, 15.0, 30.0]);
        axis_y["domain_max"] = json!(30.0);
        axis_y["grid"]["visible"] = json!(true);
        axis_y["grid"]["threshold"] = json!({"color": "#8A8C8F", "width": 2.0});
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let bands = bar_bands(&svg, true);
        assert_eq!(bands.len(), 4, "{bands:?}");
        let sub = bands[1].0 - bands[0].0;
        let step = bands[2].0 - bands[0].0;
        for (_, h) in &bands {
            assert!((h - 1.25 * sub).abs() < 0.02, "both signs take 1.25 × sub-step: {bands:?}");
        }
        assert!((bands[0].0 - (0.2 * step - 0.125 * sub)).abs() < 0.02, "centered on the sub-band: {bands:?}");
        let zero_x = zero_rule_x(&svg);
        let rects = parse_rects(&svg);
        assert_eq!(rects.len(), 4, "{rects:?}");
        assert!(rects[..2].iter().all(|r| (r.0 - zero_x).abs() < 0.1), "positive bars start at the rule: {rects:?} / {zero_x}");
        assert!(rects[2..].iter().all(|r| (r.0 + r.2 - zero_x).abs() < 0.1), "negative bars end at the rule: {rects:?} / {zero_x}");
    }

    /// An authored `overlap` keyword maps through production's table:
    /// `flush` is band 1 (touching, no offset); a positive overlap with
    /// three series and `full` are refused.
    #[test]
    fn authored_overlap_keywords_map_and_the_unsupported_ones_refuse() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("horizontal", "none");
        payload_json["resolved"]["style"]["overlap"] = json!("flush");
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        let bands = bar_bands(&svg, true);
        let sub = bands[1].0 - bands[0].0;
        let step = bands[2].0 - bands[0].0;
        assert!((bands[0].1 - sub).abs() < 0.02 && (bands[0].0 - 0.2 * step).abs() < 0.02, "flush: {bands:?}");

        let mut payload_json = make_stacked_payload("horizontal", "none");
        payload_json["resolved"]["style"]["overlap"] = json!("partial");
        payload_json["rows"] = json!([
            {"cat": "A", "series": "x", "val": 10.0}, {"cat": "A", "series": "y", "val": 30.0}, {"cat": "A", "series": "z", "val": 20.0}
        ]);
        let err = production_errors(&make_payload(payload_json)).unwrap_err();
        assert_eq!((err.code, err.params["n_series"].as_u64()), ("ERR-BAR-OVERLAP-SERIES-COUNT", Some(3)));
        let mut payload_json = make_stacked_payload("horizontal", "none");
        payload_json["resolved"]["style"]["overlap"] = json!("touching");
        let err = render(&make_payload(payload_json), &test_fonts()).unwrap_err();
        assert!(err.contains("keyword"), "{err}");
        // A bare fraction passes through; production clamps the band at
        // 0.05, so a −0.99 gap draws 0.05 × sub-step slivers.
        let mut payload_json = make_stacked_payload("horizontal", "none");
        payload_json["resolved"]["style"]["overlap"] = json!(-0.99);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        let bands = bar_bands(&svg, true);
        let sub = bands[1].0 - bands[0].0;
        assert!((bands[0].1 - 0.05 * sub).abs() < 0.02, "band clamp: {bands:?}");
    }

    /// A right legend is Vega's legend group at the side axes' ink edge +
    /// 18 (`legend.offset`; a right value axis pushes it out by its own
    /// width, a rule mark at the far edge does not), its entries on the
    /// one-column entry grid (rows 0/16/32, size-12 cells, 10px square
    /// symbols at 6, labels at 20), a title band of `titleFontSize + 5`
    /// above them (17 at 12px, 19 at 14px) with the title at baseline top,
    /// and a box `ceil(max(title, entries))` wide that is the view's
    /// rightmost ink, so the plot ends exactly `18 + box` short of the
    /// padded edge. `series-label-placement`'s
    /// `horizontal_stacked_no_endpoint_labels`: legend at 405 = 387 + 18,
    /// box 58 × 78 for four 11px rows under a 12px title.
    #[test]
    fn right_legend_takes_vega_entry_grid_at_the_axis_ink_edge() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("horizontal", "zero");
        // One entry wide enough to size the box (the corpus shape), and
        // category A's total equal to the baked domain so its bar ends at
        // the plot's right edge.
        payload_json["rows"] = json!([
            {"cat": "A", "series": "Normal", "val": 10.0},
            {"cat": "A", "series": "Urgent priority", "val": 30.0},
            {"cat": "B", "series": "Normal", "val": 5.0},
            {"cat": "B", "series": "Urgent priority", "val": 15.0}
        ]);
        payload_json["resolved"]["stacked_domain_max"] = json!(40.0);
        payload_json["resolved"]["legend"]["visible"] = json!(true);
        payload_json["resolved"]["legend"]["position"] = json!({"edge": "right", "align": "start", "overlay": false});
        payload_json["resolved"]["legend"]["direction"] = json!("vertical");
        payload_json["resolved"]["legend"]["title"] =
            json!({"font": {"family": TEST_FONT, "size": 12.0, "weight": 600.0, "case": "title"}});
        let fonts = test_fonts();
        let svg = render(&make_payload(payload_json), &fonts).expect("renders");

        let attr = |tag: &str, name: &str| -> f64 {
            tag.split(&format!(" {name}=\"")).nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        let text_tag = |label: &str| -> String {
            let pos = svg.find(&format!(">{label}</text>")).unwrap_or_else(|| panic!("no {label}: {svg}"));
            svg[svg[..pos].rfind("<text").unwrap()..pos].to_string()
        };
        let (plot_left, plot_top): (f64, f64) = {
            let t = svg.split("<g transform=\"translate(").nth(1).unwrap();
            let mut it = t.split(')').next().unwrap().split(',');
            (it.next().unwrap().parse().unwrap(), it.next().unwrap().parse().unwrap())
        };
        let entries_w = 12.0 + 8.0 + fonts.measure("Urgent priority", TEST_FONT, 11.0).unwrap();
        assert!(entries_w > fonts.measure("Series", TEST_FONT, 12.0).unwrap(), "the entries size the box");
        let box_w = entries_w.ceil();
        // The legend is the rightmost ink: the 400px fixture's padded edge
        // (384) − box.
        let legend_x = 384.0 - box_w;
        // The plot ends one 18px offset before the box: category A's bar
        // spans the whole baked domain.
        let plot_right = plot_left
            + parse_rects(&svg).iter().filter(|r| r.2 != 10.0).map(|r| r.0 + r.2).fold(0.0, f64::max);
        assert_eq!(plot_right, legend_x - 18.0, "plot right edge: {svg}");
        let title = text_tag("Series");
        assert_eq!(attr(&title, "x"), legend_x, "title x: {title}");
        assert_eq!(attr(&title, "y"), plot_top + 9.0, "title at baseline top, round(0.79·12): {title}");
        assert_eq!(attr(&title, "font-weight"), 600.0, "title weight: {title}");
        let mut rows: Vec<f64> =
            ["Normal", "Urgent priority"].iter().map(|l| attr(&text_tag(l), "y")).collect();
        rows.sort_by(f64::total_cmp);
        let entry_top = plot_top + 17.0;
        // Label at middle baseline in a size-12 cell: row + 6 + round(0.3·11).
        assert_eq!(rows, vec![entry_top + 9.0, entry_top + 25.0], "entry rows 0/16 under the 17px title band");
        assert_eq!(attr(&text_tag("Normal"), "x"), legend_x + 20.0, "label at size + labelOffset");
        let symbol = svg
            .match_indices("<rect")
            .map(|(i, _)| svg[i..].split("/>").next().unwrap().to_string())
            .find(|t| t.contains("width=\"10\" height=\"10\""))
            .unwrap_or_else(|| panic!("no square symbol: {svg}"));
        assert_eq!(attr(&symbol, "x"), legend_x + 1.0, "symbol centered at 6: {symbol}");
        assert_eq!(attr(&symbol, "y"), entry_top + 1.0, "symbol centered at 6: {symbol}");
    }

    /// With no baked ladder the shared x axis takes Vega-Lite's default
    /// `tickCount`, `ceil(width/40)` at the pane's final width, on the
    /// niced domain — `series-label-placement`'s stacked cases draw 13
    /// rungs at step 100 on 0–1.2k where a fixed count gives three.
    #[test]
    fn top_rail_unbaked_ladder_takes_vega_lite_default_tick_count() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("horizontal", "zero");
        payload_json["resolved"]["style"]["endpoint_labels"] =
            json!({"visible": true, "height": 20.0, "label_offset": 6.0});
        payload_json["resolved"]["style"]["axis_y"]["tick_values"] = json!([]);
        payload_json["resolved"]["style"]["axis_y"]["ticks"]["count"] = json!(null);
        payload_json["resolved"]["style"]["axis_y"]["grid"]["visible"] = json!(true);
        payload_json["resolved"]["stacked_domain_max"] = json!(1193.4);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        // A ~350px pane: ceil(350/40) = 9 → d3 step 100 on [0, 1200]: twelve
        // gridlines above zero plus tick 0's (the labels themselves thin by
        // parity at that pitch, so the ladder is read through the grid).
        assert_eq!(svg.matches("stroke=\"#D0D3D9\"").count(), 13, "thirteen 100-step rungs: {svg}");
        assert!(svg.contains(">1,200</text>"), "the niced end rung: {svg}");
    }

    /// The hoisted title anchors at pane 0's bounds, so a left legend pulls it to
    /// the card's padding edge instead of leaving it at the axis ink.
    #[test]
    fn a_vertical_rail_title_anchors_left_of_a_left_legend() {
        use serde_json::json;
        let mut payload_json = make_rail_payload();
        payload_json["resolved"]["legend"]["visible"] = json!(true);
        payload_json["resolved"]["legend"]["position"] =
            json!({"edge": "left", "align": "start", "overlay": false});
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        let tags = text_tags(&svg);
        let title_x = tags.iter().find(|t| t.3 == "Stacked Test").expect("title").0;
        let legend_x = tags.iter().find(|t| t.3 == "Series").expect("legend title").0;
        assert!(title_x <= legend_x && title_x < 17.0, "title {title_x} not right of legend {legend_x}: {svg}");
    }

    /// A top or bottom legend beside a vertical rail is Vega's concat child: the
    /// canvas grows to hold it instead of the legend overhanging the card.
    #[test]
    fn a_vertical_rail_holds_a_bottom_legend_inside_the_canvas() {
        use serde_json::json;
        let render_edge = |edge: &str| {
            let mut payload_json = make_rail_payload();
            payload_json["resolved"]["legend"]["visible"] = json!(true);
            payload_json["resolved"]["legend"]["position"] =
                json!({"edge": edge, "align": "start", "overlay": false});
            render(&make_payload(payload_json), &test_fonts()).expect("renders")
        };
        let canvas_h = |svg: &str| -> f64 {
            svg.split("height=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        let (bottom, top) = (render_edge("bottom"), render_edge("top"));
        let legend_y = |svg: &str| text_tags(svg).into_iter().find(|t| t.3 == "Series").expect("legend title").1;
        assert!(legend_y(&bottom) < canvas_h(&bottom), "bottom legend inside the canvas: {bottom}");
        assert!(legend_y(&bottom) > legend_y(&top), "bottom legend below the top one");
    }

    /// Value labels that author their own font size leave the rail on the concat
    /// pane: it still paints in `series_label`'s font, not the value labels'.
    #[test]
    fn top_rail_with_an_authored_value_label_size_keeps_the_series_label_font() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("horizontal", "zero");
        payload_json["resolved"]["style"]["endpoint_labels"] =
            json!({"visible": true, "height": 20.0, "label_offset": 6.0});
        payload_json["resolved"]["style"]["mark"]["labels"] = json!({
            "visible": false, "field": null, "format": null, "dx": null, "dy": null,
            "font": {"family": TEST_FONT, "size": 14.0}, "position": "top"
        });
        payload_json["resolved"]["style"]["series_label"] = json!({
            "font_family": TEST_FONT, "font_size": 11.0, "font_weight": "400",
            "font_style": "normal", "dark_companion_palette": ["#111111", "#222222"], "pin_companions": []
        });
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders on the rail concat");
        let rail = rail_label_order(&svg);
        assert_eq!(rail.len(), 2, "rail labels still drawn: {svg}");
        let tag_end = svg.find("fill=\"#111111\"").unwrap();
        let tag = &svg[svg[..tag_end].rfind("<text").unwrap()..tag_end];
        assert!(tag.contains(" y=\"53.00\"") && tag.contains(" font-size=\"11\""), "concat pane: {tag}");
    }

    /// A resolver-set `legend.columns` (compact mode on a narrow card) lays
    /// the top legend out in Vega's entry grid (`gridLayout` with the
    /// parser's `{align: each, columns, center: {row: true}, padding:
    /// {row: 2, column: 10}}`): entry size `max(ceil(sqrt(100) + 1.5), fs)`
    /// = 12 at 11px, symbol bounds −0.5…12.5 with the 1.5px default symbol
    /// stroke, so row pitch = 2 + ceil(0.5) + ceil(12.5) = 16, the first
    /// row centered by (13 − 12.5)/2 = 0.25 and later rows not (Vega
    /// subtracts the accumulated offset in that test), box height
    /// ceil(16 + 12.5) = 29 — 16 more than the one-row 13, and the plot
    /// moves down by exactly that.
    #[test]
    fn top_legend_wraps_into_vega_entry_grid_when_columns_is_set() {
        use serde_json::json;
        let plot_top = |svg: &str| -> f64 {
            let t = svg.split("<g transform=\"translate(").nth(1).unwrap();
            t.split(')').next().unwrap().split(',').nth(1).unwrap().parse().unwrap()
        };
        let swatch_ys = |svg: &str| -> Vec<f64> {
            let mut ys: Vec<f64> = svg
                .split("<rect ")
                .skip(1)
                .filter(|r| r.contains("width=\"10\" height=\"10\""))
                .map(|r| r.split(" y=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap())
                .collect();
            ys.dedup();
            ys
        };
        let one_row = render(&make_payload(make_three_series_stacked_payload("horizontal")), &test_fonts()).expect("renders");
        let mut wrapped = make_three_series_stacked_payload("horizontal");
        wrapped["resolved"]["legend"]["columns"] = json!(2);
        let grid = render(&make_payload(wrapped), &test_fonts()).expect("renders");
        assert_eq!(plot_top(&grid) - plot_top(&one_row), 16.0, "a second legend row costs 16px");
        let ys = swatch_ys(&grid);
        assert_eq!(ys.len(), 2, "two swatch rows: {ys:?}");
        assert!((ys[1] - ys[0] - 15.75).abs() < 1e-9, "rows at 0.25 and 16: {ys:?}");
    }

    /// The vertical arm reserves the same grid: a one-column legend of the
    /// grouped fixture's two series stacks two rows (0 and 16, box 29) and
    /// the plot drops 16px against the one-row render.
    #[test]
    fn vertical_top_legend_wraps_into_the_entry_grid_too() {
        use serde_json::json;
        let plot_top = |svg: &str| -> f64 {
            let t = svg.split("<g transform=\"translate(").nth(1).unwrap();
            t.split(')').next().unwrap().split(',').nth(1).unwrap().parse().unwrap()
        };
        let cats = [("A", 10.0), ("B", 20.0)];
        let one_row = render(&make_payload(make_vertical_payload(&cats, true)), &test_fonts()).expect("renders");
        let mut stacked = make_vertical_payload(&cats, true);
        stacked["resolved"]["legend"]["columns"] = json!(1);
        let grid = render(&make_payload(stacked), &test_fonts()).expect("renders");
        assert_eq!(plot_top(&grid) - plot_top(&one_row), 16.0, "a second legend row costs 16px");
    }

    /// A color channel that is 1:1 with the category — every band a single
    /// bar in its own color — is not a grouped bar: production's
    /// `_is_color_1to1_with_x` suppresses the offset channel, so the bar
    /// takes the plain band (`boards/content_0/revenue_chart`: 30.67px bars
    /// in 38.33px bands, not thirds). The bars must match the same data
    /// drawn single-series.
    #[test]
    fn color_one_to_one_with_category_takes_the_full_band() {
        use serde_json::json;
        let rows = json!([
            {"product": "Zebra", "revenue": 800.0},
            {"product": "Apple", "revenue": 600.0},
            {"product": "Mango", "revenue": 700.0}
        ]);
        let mut plain = make_horizontal_payload();
        plain["rows"] = rows.clone();
        let mut colored = make_horizontal_payload();
        colored["rows"] = rows;
        colored["resolved"]["color"] = json!("product");
        colored["resolved"]["palette"] = json!(["#3164a3", "#779bc9", "#ad9c7f"]);
        let heights = |svg: &str| -> Vec<f64> {
            let mut hs: Vec<f64> = parse_rects(svg).iter().filter(|r| r.2 > 20.0 && r.3 > 2.0 && r.3 < 200.0).map(|r| r.3).collect();
            hs.sort_by(|a, b| a.partial_cmp(b).unwrap());
            hs
        };
        let plain_svg = render(&make_payload(plain), &test_fonts()).expect("renders");
        let colored_svg = render(&make_payload(colored), &test_fonts()).expect("renders");
        let (hp, hc) = (heights(&plain_svg), heights(&colored_svg));
        assert_eq!(hp.len(), 3, "three plain bars: {hp:?}");
        assert_eq!(hp, hc, "1:1 color keeps the single-series band: plain {hp:?} vs colored {hc:?}\n{colored_svg}");
    }

    /// The vertical arm's 1:1 gate — same rule, column widths.
    #[test]
    fn color_one_to_one_with_category_takes_the_full_band_vertical() {
        use serde_json::json;
        let cats = [("Zebra", 800.0), ("Apple", 600.0), ("Mango", 700.0)];
        // Square corners so the columns are rects, not rounded paths.
        let mut plain = make_vertical_payload(&cats, false);
        plain["resolved"]["style"]["mark"]["border"]["radius"] = json!(0.0);
        let mut colored = plain.clone();
        colored["resolved"]["color"] = json!("product");
        colored["resolved"]["palette"] = json!(["#3164a3", "#779bc9", "#ad9c7f"]);
        let widths = |svg: &str| -> Vec<f64> {
            let mut ws: Vec<f64> = parse_rects(svg).iter().filter(|r| r.3 > 20.0 && r.2 > 2.0 && r.2 < 200.0).map(|r| r.2).collect();
            ws.sort_by(|a, b| a.partial_cmp(b).unwrap());
            ws
        };
        let plain_svg = render(&make_payload(plain), &test_fonts()).expect("renders");
        let colored_svg = render(&make_payload(colored), &test_fonts()).expect("renders");
        let (wp, wc) = (widths(&plain_svg), widths(&colored_svg));
        assert_eq!(wp.len(), 3, "three plain columns: {wp:?}");
        assert_eq!(wp, wc, "1:1 color keeps the single-series band: plain {wp:?} vs colored {wc:?}\n{colored_svg}");
    }

    /// A value axis with no baked ladder, no authored tick count and no
    /// baked domain renders on Vega-Lite's defaults: the headroomed max is
    /// niced (d3, count 10) and the axis draws d3 ticks at
    /// `tickCount = ceil(range/40)` on the fitted plot extent
    /// (`quick-guide_7/revenue_by_month`: 0–180k at 10k steps on a 504px
    /// plot). Here a 164,000 max nices to 180,000 and the fixture's ~550px
    /// plot takes ceil(550/40) = 14 → 10,000 steps, 19 rungs; the capped
    /// `numeric.py` ladder drew 0–150,000 in 50,000 steps over an un-niced
    /// 164,000.
    #[test]
    fn unbaked_value_axis_takes_vega_lite_defaults() {
        use serde_json::json;
        let mut p = make_horizontal_payload();
        p["rows"] = json!([
            {"product": "Zebra", "revenue": 164000.0},
            {"product": "Apple", "revenue": 60000.0},
            {"product": "Mango", "revenue": 90000.0}
        ]);
        p["resolved"]["style"]["axis_y"]["tick_values"] = json!([]);
        p["resolved"]["style"]["axis_y"]["ticks"]["count"] = json!(null);
        p["resolved"]["style"]["axis_y"]["domain_max"] = json!(null);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        // Eighteen gridlines above zero plus tick 0's (the labels thin by
        // parity at that pitch, so the ladder is read through the grid). The flush end
        // label trips a second parity round, so every fourth label stays
        // — the `quick-guide_7` reference's 0/40k/…/160k.
        assert_eq!(svg.matches("stroke=\"#D0D3D9\"").count(), 19, "19 thousands rungs: {svg}");
        assert!(svg.contains(">160,000</text>") && !svg.contains(">180,000</text>"), "every fourth label: {svg}");
    }

    /// A baked `domainMax` switches Vega-Lite's nice default OFF: a stacked
    /// bar's unit spec carries `domainMax = stacked_domain_max` and its axis
    /// reads "0 to 697" (`series-label-placement/horizontal_stacked_no_
    /// endpoint_labels`), rungs 0–650 by 50 with no 700 rung. Only an axis
    /// Vega receives no bound for is niced; the concat rail arms nice
    /// their shared scale separately. This fixture's narrower plot takes
    /// 100-steps, so the discriminator is the 700 rung a niced domain
    /// would add.
    #[test]
    fn unbaked_stacked_axis_keeps_its_baked_domain_max_unniced() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("horizontal", "zero");
        payload_json["resolved"]["style"]["axis_y"]["tick_values"] = json!([]);
        payload_json["resolved"]["style"]["axis_y"]["ticks"]["count"] = json!(null);
        payload_json["resolved"]["stacked_domain_max"] = json!(696.6);
        payload_json["rows"] = json!([
            {"cat": "A", "series": "x", "val": 300.0},
            {"cat": "A", "series": "y", "val": 345.0},
            {"cat": "B", "series": "x", "val": 100.0},
            {"cat": "B", "series": "y", "val": 200.0}
        ]);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        assert!(svg.contains(">600</text>") && !svg.contains(">700</text>"), "0–600, no 700 on a 696.6 domain: {svg}");
    }

    /// `rail_concat`'s own gate excluded an unbaked value axis
    /// (`ax_y.tick_values.is_empty()`), reasoning that the arm's ladder
    /// re-filter needs a baked ladder to filter. Production draws no such
    /// line: instrumenting `render_resolved_chart` directly on
    /// `playground/dundersign-support-operations/hero_queue_age` (a
    /// stacked vertical bar, visible endpoint labels, NO baked
    /// `axis_y.tick_values`) shows production compiles this to the same
    /// `hconcat`/`autosize: fit` rail every OTHER rail-eligible stacked bar
    /// gets — there is no separate "legacy" path for an unbaked axis.
    /// `niced_rail_domain` (this file, ~line 1145) already handles an empty
    /// `ax_y.tick_values` correctly (nices the domain, then generates a
    /// fresh d3-ticks ladder from it) — the gate's exclusion was excess,
    /// stale caution, not a real production distinction.
    ///
    /// Regression shape: a stacked total of 100.44 nices to 110
    /// (`d3_nice_domain(0, 100.44, 10) == 110`) ONLY inside the rail arm's
    /// own `niced_rail_domain` — the legacy arm's `value_domain_and_ticks`
    /// never nices a bound (baked-`domainMax`) stacked axis (see the
    /// un-niced 696.6 test above). Deletion counterfactual: restoring the
    /// `(normalize || !ax_y.tick_values.is_empty())` clause to
    /// `rail_concat` routes this payload to the legacy arm instead,
    /// which stops at the un-niced 100 — this test fails on that revert.
    #[test]
    fn rail_concat_admits_an_unbaked_value_axis() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("vertical", "zero");
        payload_json["resolved"]["style"]["axis_y"]["tick_values"] = json!([]);
        payload_json["resolved"]["style"]["axis_y"]["ticks"]["count"] = json!(null);
        payload_json["resolved"]["stacked_domain_max"] = json!(100.44);
        payload_json["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
        payload_json["resolved"]["style"]["series_label"]["gap_px"] = json!(8.0);
        payload_json["height"] = json!(600.0);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        assert!(svg.contains(">110</text>"), "rail arm must nice 100.44 to 110: {svg}");
    }

    /// A rail whose unbaked axis authors `ticks.count` draws that many d3
    /// ticks (production forwards the count as `tickCount`), not an empty
    /// ladder: count 6 on 0–1200 steps by 200.
    #[test]
    fn top_rail_unbaked_ladder_honors_an_authored_tick_count() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("horizontal", "zero");
        payload_json["resolved"]["style"]["endpoint_labels"] =
            json!({"visible": true, "height": 20.0, "label_offset": 6.0});
        payload_json["resolved"]["style"]["axis_y"]["tick_values"] = json!([]);
        payload_json["resolved"]["style"]["axis_y"]["ticks"]["count"] = json!(6.0);
        payload_json["resolved"]["stacked_domain_max"] = json!(1193.4);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        assert!(svg.contains(">200</text>") && svg.contains(">1,200</text>") && !svg.contains(">100</text>"), "0–1,200 by 200: {svg}");
    }

    /// A rail beside a right legend lays the legend out as Vega's entry grid in
    /// the right gutter.
    #[test]
    fn a_right_legend_beside_a_rail_sits_in_the_right_gutter() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("horizontal", "zero");
        payload_json["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
        payload_json["resolved"]["legend"] = json!({
            "visible": true,
            "position": {"edge": "right", "align": "start", "overlay": false},
            "direction": "vertical",
            "title": {"font": {"case": "title", "family": TEST_FONT, "size": 12.0, "weight": "600"}},
            "label": {"padding": 8.0, "font": {"family": TEST_FONT, "color": "#626366", "size": 11.0, "weight": "400"}}
        });
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        assert!(svg.contains(">Series<"), "right legend carries its title: {svg}");
        // The legend sits in the right gutter; the rail's labels stay above the plot.
        let legend_x = text_tags(&svg).into_iter().find(|t| t.3 == "Series").expect("legend title").0;
        assert!(legend_x > 200.0 && legend_x < 400.0, "legend title at {legend_x}: {svg}");
        let rail = text_tags(&svg).into_iter().filter(|t| (t.3 == "x" || t.3 == "y") && t.0 < legend_x).count();
        assert_eq!(rail, 2, "one rail label per series left of the legend: {svg}");
    }

    /// Every `<text>` tag as `(x, y, text-anchor, content)`.
    fn text_tags(svg: &str) -> Vec<(f64, f64, String, String)> {
        svg.split("<text ")
            .skip(1)
            .filter_map(|t| {
                let tag = format!(" {t}");
                let t = tag.as_str();
                let attr = |name: &str| Some(t.split(&format!(" {name}=\"")).nth(1)?.split('"').next()?.to_string());
                let content = t.split('>').nth(1)?.split('<').next()?.to_string();
                Some((attr("x")?.parse().ok()?, attr("y")?.parse().ok()?, attr("text-anchor").unwrap_or_default(), content))
            })
            .collect()
    }

    /// A bottom legend anchors on `yBounds`, the plot rect ∪ the x axis's
    /// own bounds (`ViewLayout.js:62-65`), at `ceil(y2) + offset`
    /// (`legend.js:58-62`): it starts 18px past the x axis's label band,
    /// never past the bare plot edge, and the fit makes it the view's
    /// bottom-most ink. One untitled 13px row: its swatches at `0.25 + 1`.
    #[test]
    fn a_bottom_legend_clears_the_x_axis_band_by_the_offset() {
        use serde_json::json;
        for orientation in ["horizontal", "vertical"] {
            let mut p = make_stacked_payload(orientation, "zero");
            p["resolved"]["legend"]["visible"] = json!(true);
            p["resolved"]["legend"]["position"] = json!({"edge": "bottom", "align": "start", "overlay": false});
            p["resolved"]["legend"]["title"]["visible"] = json!(false);
            let svg = render(&make_payload(p), &test_fonts()).unwrap_or_else(|e| panic!("{orientation}: {e}"));
            let legend_y = 300.0 - 16.0 - 13.0;
            let swatch_ys: Vec<f64> = svg
                .split("<rect ")
                .skip(1)
                .filter(|r| r.contains("width=\"10\" height=\"10\""))
                .map(|r| r.split(" y=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap())
                .collect();
            assert_eq!(swatch_ys, vec![legend_y + 1.25; 2], "{orientation}: {svg}");
            // The x axis's lowest label (top baseline, 11px) ends 18px above
            // the legend, give or take the ceil.
            let axis_label_y = text_tags(&svg)
                .iter()
                .filter(|(_, _, _, text)| !["x", "y"].contains(&text.as_str()))
                .map(|t| t.1)
                .fold(f64::NEG_INFINITY, f64::max);
            let gap = legend_y - axis_label_y;
            assert!(gap >= 18.0 && gap < 18.0 + 11.0 + 1.0, "{orientation}: gap {gap}: {svg}");
        }
    }

    #[test]
    fn normalize_stack_sums_to_full_plot_extent_regardless_of_category_total() {
        // Category "A" totals 40, "B" totals 20 — a normalize stack must
        // still fill the SAME fraction of the plot for both.
        let payload = make_payload(make_stacked_payload("vertical", "normalize"));
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("normalize stack renders");

        assert!(svg.contains("0%"), "0% tick label present");
        assert!(svg.contains("100%"), "100% tick label present");

        let rects = parse_rects(&svg);
        assert_eq!(rects.len(), 4);
        let total_height_per_cat: Vec<f64> = {
            let mut by_x: HashMap<i64, f64> = HashMap::new();
            for (x, _, _, h, _) in &rects {
                *by_x.entry((*x * 100.0).round() as i64).or_insert(0.0) += h;
            }
            by_x.into_values().collect()
        };
        assert_eq!(total_height_per_cat.len(), 2, "two distinct category columns");
        let (h0, h1) = (total_height_per_cat[0], total_height_per_cat[1]);
        assert!(
            (h0 - h1).abs() < 0.5,
            "both categories fill the same total plot extent under normalize: {h0} vs {h1}"
        );
    }

    /// Legend display order for a stacked bar follows the STACK order
    /// (descending global sum), not alphabetical — and vertical reverses it
    /// relative to horizontal. Adds a third series ("z", sum 25) so the
    /// stack order ([y, z, x]) and its reverse ([x, z, y]) both differ
    /// visibly from the alphabetical fallback ([x, y, z]).
    fn make_three_series_stacked_payload(orientation: &str) -> serde_json::Value {
        use serde_json::json;
        let mut payload_json = make_stacked_payload(orientation, "zero");
        payload_json["rows"] = json!([
            {"cat": "A", "series": "x", "val": 10.0},
            {"cat": "A", "series": "y", "val": 30.0},
            {"cat": "A", "series": "z", "val": 20.0},
            {"cat": "B", "series": "x", "val": 5.0},
            {"cat": "B", "series": "y", "val": 15.0},
            {"cat": "B", "series": "z", "val": 5.0},
        ]);
        payload_json["resolved"]["palette"] = json!(["#3164a3", "#779bc9", "#ad9c7f"]);
        payload_json["resolved"]["legend"]["visible"] = json!(true);
        payload_json["resolved"]["stacked_domain_max"] = json!(70.0);
        payload_json
    }

    /// `<text>` labels in first-appearance order (crude, but the legend loop
    /// writes swatch + label pairs strictly left-to-right).
    fn legend_label_order(svg: &str, candidates: &[&str]) -> Vec<String> {
        let mut found: Vec<(usize, &str)> = candidates
            .iter()
            .filter_map(|&c| svg.find(&format!(">{c}</text>")).map(|pos| (pos, c)))
            .collect();
        found.sort_by_key(|&(pos, _)| pos);
        found.into_iter().map(|(_, c)| c.to_string()).collect()
    }

    fn legend_entries(svg: &str, candidates: &[&str]) -> Vec<(String, String)> {
        let mut found: Vec<(usize, String, String)> = candidates
            .iter()
            .filter_map(|&label| {
                let label_pos = svg.find(&format!(">{label}</text>"))?;
                let rect_pos = svg[..label_pos].rfind("<rect ")?;
                let swatch = &svg[rect_pos..label_pos];
                let fill = swatch
                    .split("fill=\"")
                    .nth(1)?
                    .split('"')
                    .next()?
                    .to_string();
                Some((label_pos, label.to_string(), fill))
            })
            .collect();
        found.sort_by_key(|(pos, _, _)| *pos);
        found
            .into_iter()
            .map(|(_, label, fill)| (label, fill))
            .collect()
    }

    #[test]
    fn horizontal_stacked_legend_order_matches_stack_order() {
        let payload = make_payload(make_three_series_stacked_payload("horizontal"));
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert_eq!(legend_label_order(&svg, &["x", "y", "z"]), vec!["y", "z", "x"]);
        assert_eq!(
            legend_entries(&svg, &["x", "y", "z"]),
            vec![
                ("y".into(), "#3164a3".into()),
                ("z".into(), "#779bc9".into()),
                ("x".into(), "#ad9c7f".into()),
            ]
        );
    }

    #[test]
    fn vertical_stacked_legend_order_is_reverse_of_stack_order() {
        let payload = make_payload(make_three_series_stacked_payload("vertical"));
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert_eq!(legend_label_order(&svg, &["x", "y", "z"]), vec!["x", "z", "y"]);
        assert_eq!(
            legend_entries(&svg, &["x", "y", "z"]),
            vec![
                ("x".into(), "#ad9c7f".into()),
                ("z".into(), "#779bc9".into()),
                ("y".into(), "#3164a3".into()),
            ]
        );
    }

    /// `stack: center` floats each category's stack to the middle of the
    /// tallest one: only the tallest keeps a segment on the baseline.
    #[test]
    fn a_centered_stack_offsets_each_category_by_half_its_shortfall() {
        let on_baseline = |stack: &str| {
            let svg = render(&make_payload(make_stacked_payload("vertical", stack)), &test_fonts()).expect("renders");
            let bottoms: Vec<f64> = parse_rects(&svg)
                .iter()
                .filter(|r| r.2 != 10.0 && r.3 != 10.0)
                .map(|r| r.1 + r.3)
                .collect();
            let floor = bottoms.iter().copied().fold(f64::MIN, f64::max);
            bottoms.iter().filter(|b| (**b - floor).abs() < 0.01).count()
        };
        assert_eq!(on_baseline("zero"), 2, "both categories rest on the baseline");
        assert_eq!(on_baseline("center"), 1, "only the tallest stack does");
    }

    /// Vega's `center` offset piles `|v|`: a negative value draws its magnitude up
    /// from the offset, never below the baseline.
    #[test]
    fn a_centered_stack_piles_a_negative_value_as_its_magnitude() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("vertical", "center");
        payload_json["rows"][0]["val"] = json!(-10.0);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        let mut bars: Vec<_> = parse_rects(&svg).into_iter().filter(|r| r.2 != 10.0 && r.3 != 10.0).collect();
        bars.sort_by(|a, b| a.0.total_cmp(&b.0));
        let first_category: Vec<_> = bars.iter().filter(|r| (r.0 - bars[0].0).abs() < 0.01).collect();
        assert_eq!(first_category.len(), 2, "{bars:?}");
        let (small, large) = (first_category[0].3.min(first_category[1].3), first_category[0].3.max(first_category[1].3));
        assert!((large / small - 3.0).abs() < 1e-6, "|-10| against 30: {bars:?}");
    }

    /// Each sign layer of a split stack centers on its own tallest pile, across every
    /// panel a facet hands over; a `middle` label's stack takes both signs at once.
    #[test]
    fn a_center_stack_takes_its_tallest_pile_per_sign_layer_across_panels() {
        let row = |cat: &str, v: f64| serde_json::json!({"c": cat, "v": v}).as_object().unwrap().clone();
        let a = vec![row("N", 100.0), row("N", -80.0), row("S", 30.0)];
        let b = vec![row("N", 60.0), row("N", 20.0)];
        let panels = || [a.as_slice(), b.as_slice()];
        assert_eq!(CenterStack::over(panels(), "c", "v", true).tallest, [100.0, 80.0]);
        assert_eq!(CenterStack::over(panels(), "c", "v", false).tallest, [180.0, 0.0]);
        assert!(CenterStack::splits(Some(2.0), &a, "v"));
        assert!(!CenterStack::splits(None, &a, "v"));
        assert!(!CenterStack::splits(Some(2.0), &b, "v"));
    }

    /// A colorless center stack is a stack of one series: each bar floats by half
    /// its shortfall from the tallest.
    #[test]
    fn a_colorless_centered_bar_floats_by_half_its_shortfall() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("vertical", "center");
        payload_json["resolved"]["color"] = json!(null);
        payload_json["rows"] = json!([{"cat": "A", "series": "x", "val": 10.0}, {"cat": "B", "series": "x", "val": 20.0}]);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        let mut bars: Vec<_> = parse_rects(&svg).into_iter().filter(|r| r.2 != 10.0 && r.3 != 10.0).collect();
        bars.sort_by(|a, b| a.0.total_cmp(&b.0));
        assert_eq!(bars.len(), 2, "{bars:?}");
        let (shorter, taller) = (&bars[0], &bars[1]);
        let unit = taller.3 / 20.0;
        assert!((shorter.3 - 10.0 * unit).abs() < 1e-6, "{bars:?}");
        assert!(((taller.1 + taller.3) - (shorter.1 + shorter.3) - 5.0 * unit).abs() < 1e-6, "{bars:?}");
    }

    /// Production raises these before drawing; the engine reports the same
    /// code and params.
    #[test]
    fn production_errors_report_the_registered_code_and_params() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("vertical", "none");
        payload_json["resolved"]["style"]["overlap"] = json!("full");
        let err = production_errors(&make_payload(payload_json)).unwrap_err();
        assert_eq!(err.code, "ERR-BAR-OVERLAP-FULL-NON-UNIQUE-COLOR");
        assert_eq!(err.params["series"], json!("series"));

        let mut payload_json = make_stacked_payload("vertical", "none");
        payload_json["resolved"]["style"]["overlap"] = json!(1.5);
        assert!(production_errors(&make_payload(payload_json)).is_err());
    }

    /// A positive overlap needs exactly two series; the count is the distinct
    /// color values, or the wide measures.
    #[test]
    fn a_positive_overlap_off_two_series_raises_the_series_count_code() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("vertical", "none");
        payload_json["resolved"]["style"]["overlap"] = json!(0.3);
        production_errors(&make_payload(payload_json.clone())).expect("two series");
        payload_json["rows"].as_array_mut().unwrap().push(json!({"cat": "A", "series": "z", "val": 1.0}));
        let err = production_errors(&make_payload(payload_json)).unwrap_err();
        assert_eq!(err.code, "ERR-BAR-OVERLAP-SERIES-COUNT");
        assert_eq!(err.params["n_series"], json!(3));
        assert_eq!(err.params["chart_id"], json!("stacked_test"));

        let mut payload_json = make_stacked_payload("vertical", "none");
        payload_json["resolved"]["style"]["overlap"] = json!("flush");
        payload_json["rows"].as_array_mut().unwrap().push(json!({"cat": "A", "series": "z", "val": 1.0}));
        production_errors(&make_payload(payload_json)).expect("no positive overlap");
    }

    /// Wide measures are series too: a full overlap names the measures.
    #[test]
    fn production_errors_cover_wide_measures() {
        use serde_json::json;
        let mut payload_json = make_wide_horizontal_payload();
        payload_json["resolved"]["style"]["overlap"] = json!("full");
        let err = production_errors(&make_payload(payload_json)).unwrap_err();
        assert_eq!(err.code, "ERR-BAR-OVERLAP-FULL-NON-UNIQUE-COLOR");
        assert_eq!(err.params["series"], json!("revenue, profit"));
    }

    /// A continuous category axis has no band to divide, so any authored
    /// overlap raises its own code ahead of the full-overlap check.
    #[test]
    fn an_authored_overlap_on_a_continuous_axis_raises_the_continuous_code() {
        use serde_json::json;
        for overlap in [json!("full"), json!("partial")] {
            let mut payload_json = make_stacked_payload("vertical", "none");
            payload_json["resolved"]["style"]["overlap"] = overlap;
            payload_json["rows"] = json!([
                {"cat": 1.5, "series": "x", "val": 10.0},
                {"cat": 1.5, "series": "y", "val": 15.0},
                {"cat": 2.5, "series": "x", "val": 12.0},
                {"cat": 2.5, "series": "y", "val": 9.0},
            ]);
            let err = production_errors(&make_payload(payload_json)).unwrap_err();
            assert_eq!(err.code, "ERR-BAR-OVERLAP-CONTINUOUS-AXIS");
            assert_eq!(err.params["axis"], json!("x"));
        }
    }

    /// Under small multiples the 1:1 rule folds per panel: each panel pairs a
    /// month with one color, so production adds no offset channel and renders.
    #[test]
    fn a_full_overlap_is_one_to_one_per_panel_under_multiples() {
        use serde_json::json;
        let mut payload_json = facet::make_facet_payload("shared");
        payload_json["resolved"]["color"] = json!("series");
        payload_json["resolved"]["style"]["overlap"] = json!("full");
        payload_json["rows"] = json!([
            {"product": "Jan", "region": "North", "series": "x", "revenue": 1.0},
            {"product": "Feb", "region": "North", "series": "y", "revenue": 2.0},
            {"product": "Jan", "region": "South", "series": "y", "revenue": 3.0},
            {"product": "Feb", "region": "South", "series": "x", "revenue": 4.0},
        ]);
        production_errors(&make_payload(payload_json.clone())).expect("each panel is 1:1");
        payload_json["rows"][1]["product"] = json!("Jan");
        let err = production_errors(&make_payload(payload_json)).unwrap_err();
        assert_eq!(err.code, "ERR-BAR-OVERLAP-FULL-NON-UNIQUE-COLOR");
    }

    /// A full overlap over a color that is one value per category is the
    /// waterfall's shape, and renders.
    #[test]
    fn a_full_overlap_over_a_unique_color_is_not_an_error() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("vertical", "none");
        payload_json["resolved"]["style"]["overlap"] = json!("full");
        payload_json["rows"] = json!([
            {"cat": "A", "series": "x", "val": 10.0},
            {"cat": "B", "series": "y", "val": 15.0},
        ]);
        production_errors(&make_payload(payload_json)).expect("one color per category");
    }

    /// `stack_order: alphabetical` ranks by name where the default ranks by
    /// descending sum, so the two disagree when the later name is the bigger.
    #[test]
    fn alphabetical_stack_order_differs_from_the_default() {
        use serde_json::json;
        let svg_for = |order: Option<&str>| {
            let mut payload_json = make_stacked_payload("vertical", "zero");
            payload_json["rows"] = json!([
                {"cat": "A", "series": "x", "val": 10.0},
                {"cat": "A", "series": "y", "val": 30.0},
                {"cat": "B", "series": "x", "val": 5.0},
                {"cat": "B", "series": "y", "val": 15.0},
            ]);
            if let Some(order) = order {
                payload_json["resolved"]["style"]["stack_order"] = json!(order);
            }
            render(&make_payload(payload_json), &test_fonts()).expect("renders")
        };
        assert_ne!(svg_for(None), svg_for(Some("alphabetical")));
    }

    #[test]
    fn stack_order_other_than_default_is_unsupported() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("vertical", "zero");
        payload_json["resolved"]["style"]["stack_order"] = json!("reverse");
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let err = render(&payload, &fonts).unwrap_err();
        assert!(err.contains("stack_order"), "error names stack_order: {err}");
    }

    // A stacked vertical bar with `legend.position: right` (the theme
    // default for a stacked series — `unconditional_top_legend` only forces
    // `top` on a non-stacked bar) must not silently draw a top-row legend:
    // the column is the view's rightmost ink, so the plot stops `18 + box`
    // short of the padded edge with the legend title at the box's left.
    #[test]
    fn vertical_stacked_bar_legend_position_right_reserves_a_column() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("vertical", "zero");
        payload_json["resolved"]["legend"] = json!({
            "visible": true,
            "position": {"edge": "right", "align": "start", "overlay": false},
            "direction": "vertical",
            "title": {"font": {"case": "title", "family": TEST_FONT, "size": 12.0, "weight": "600"}},
            "label": {"padding": 8.0, "font": {"family": TEST_FONT, "color": "#626366", "size": 11.0, "weight": "400"}}
        });
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        let title_x: f64 = svg
            .split(">Series<")
            .next()
            .and_then(|s| s.rsplit("x=\"").next())
            .and_then(|s| s.split('"').next())
            .and_then(|s| s.parse().ok())
            .expect("right legend carries its title");
        let entries_w = 20.0 + fonts.measure("x", TEST_FONT, 11.0).unwrap().max(fonts.measure("y", TEST_FONT, 11.0).unwrap());
        let box_w = entries_w.max(fonts.measure("Series", TEST_FONT, 12.0).unwrap()).ceil();
        assert_eq!(title_x, 384.0 - box_w, "legend box at the padded edge: {svg}");
        assert_eq!(
            svg.matches("width=\"10\" height=\"10\"").count(),
            2,
            "one square symbol per series: {svg}"
        );
        // The width reservation is the feature: the bars end at or before
        // the plot's right edge, 18px (Vega's legend offset) left of the box.
        let plot_left: f64 = svg
            .split("<g transform=\"translate(")
            .nth(1)
            .and_then(|s| s.split(',').next())
            .and_then(|s| s.parse().ok())
            .expect("plot group has a translate(x,y)");
        let rects = parse_rects(&svg);
        let max_extent = plot_left
            + rects.iter().filter(|r| r.2 != 10.0).map(|(x, _, w, _, _)| x + w).fold(0.0_f64, f64::max);
        assert!(
            max_extent <= title_x - 18.0,
            "bars end before the legend offset (box at {title_x}), got {max_extent}: {svg}"
        );
    }

    #[test]
    fn vertical_bar_right_legend_clears_the_right_axis_label_gutter() {
        // val axis on the RIGHT (the fixture default is left): Vega places
        // the legend at the axis ink's right edge + 18, so the column sits
        // exactly one offset past the tick-label gutter. Anchoring the
        // legend at the plot edge instead paints it over the tick labels.
        use serde_json::json;
        let mut payload_json = make_stacked_payload("vertical", "zero");
        payload_json["resolved"]["style"]["axis_y"]["position"] = json!("right");
        payload_json["resolved"]["legend"] = json!({
            "visible": true,
            "position": {"edge": "right", "align": "start", "overlay": false},
            "direction": "vertical",
            "title": {"font": {"case": "title", "family": TEST_FONT, "size": 12.0, "weight": "600"}},
            "label": {"padding": 8.0, "font": {"family": TEST_FONT, "color": "#626366", "size": 11.0, "weight": "400"}}
        });
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        let title_x: f64 = svg
            .split(">Series<")
            .next()
            .and_then(|s| s.rsplit("x=\"").next())
            .and_then(|s| s.split('"').next())
            .and_then(|s| s.parse().ok())
            .expect("legend title has an x");
        // The tick labels are end-anchored at the gutter's far edge, drawn
        // inside the axis's +0.5 crisp translate that Vega's axis bounds
        // leave out. Ticks are numeric; the legend's own 11px labels are
        // not.
        let tick_right: f64 = {
            let mut best = f64::NEG_INFINITY;
            for (pos, _) in svg.match_indices("<text ") {
                let tag_end = svg[pos..].find("</text>").map(|i| pos + i).unwrap_or(svg.len());
                let tag = &svg[pos..tag_end];
                if !tag.contains("font-size=\"11\"") || !tag.contains("text-anchor=\"end\"") {
                    continue;
                }
                let Some(content) = tag.rsplit('>').next().map(str::trim) else { continue };
                if content.trim_end_matches(['K', 'k', ' ']).parse::<f64>().is_err() {
                    continue;
                }
                let x: f64 = tag.split(" x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
                best = best.max(x);
            }
            best
        };
        assert!(tick_right.is_finite(), "right-axis tick labels present:\n{svg}");
        assert_eq!(
            title_x,
            (tick_right - 0.5).ceil() + 18.0,
            "legend at ceil(axis ink edge) + 18 (labels end at {tick_right}):\n{svg}"
        );
    }

    /// A left legend anchors on `xBounds`, the plot rect ∪ the y axis's own
    /// bounds (`ViewLayout.js:62-65`), at `floor(x1) - offset - box_w`
    /// (`legend.js:41-46`): titled, it is the view's leftmost ink (its title
    /// at the padded edge) and ends 18px short of the value axis's widest
    /// label, give or take the floor. A bar is a bare mark, never in
    /// `xBounds`.
    #[test]
    fn a_left_legend_clears_the_y_axis_labels_by_the_offset() {
        use serde_json::json;
        let fonts = test_fonts();
        let mut p = make_stacked_payload("vertical", "zero");
        p["resolved"]["legend"]["visible"] = json!(true);
        p["resolved"]["legend"]["position"] = json!({"edge": "left", "align": "start", "overlay": false});
        p["resolved"]["legend"]["direction"] = json!("vertical");
        p["resolved"]["legend"]["title"] =
            json!({"font": {"family": TEST_FONT, "size": 12.0, "weight": 600.0, "case": "title"}});
        let svg = render(&make_payload(p), &fonts).expect("renders");
        let tags = text_tags(&svg);
        let title = tags.iter().find(|t| t.3 == "Series").unwrap_or_else(|| panic!("no title: {svg}"));
        assert_eq!(title.0, 16.0, "the legend is the leftmost ink: {svg}");
        let box_w = (12.0 + 8.0 + fonts.measure_shaped("y", TEST_FONT, 11.0).unwrap())
            .max(fonts.measure_shaped("Series", TEST_FONT, 12.0).unwrap())
            .ceil();
        let axis_x1 = tags
            .iter()
            .filter(|t| t.2 == "end" && ["0", "25", "50"].contains(&t.3.as_str()))
            .map(|t| t.0 - fonts.measure(&t.3, TEST_FONT, 11.0).unwrap())
            .fold(f64::INFINITY, f64::min);
        let gap = axis_x1 - (16.0 + box_w);
        assert!(gap >= 18.0 && gap < 19.0 + 1.0, "gap {gap}: {svg}");
    }

    #[test]
    fn stack_zero_missing_baked_domain_max_takes_the_ladders_last_rung() {
        // `emitters/bar.py`: a stack with no baked total takes its domain top from
        // the baked ladder's last rung; with neither, there is nothing to read.
        let mut payload_json = make_stacked_payload("vertical", "zero");
        payload_json["resolved"]["stacked_domain_max"] = Value::Null;
        let payload = make_payload(payload_json.clone());
        render(&payload, &test_fonts()).expect("the ladder's last rung (50) is the top");
        payload_json["resolved"]["style"]["axis_y"]["tick_values"] = serde_json::json!([]);
        let err = render(&make_payload(payload_json), &test_fonts()).unwrap_err();
        assert!(err.contains("stacked_domain_max"), "error names the missing field: {err}");
    }

    /// Vega's "normalize" divides by the ABSOLUTE stacked sum and stacks each sign in its
    /// own layer, so a negative segment hangs below the zero line as a share of that sum.
    #[test]
    fn stack_normalize_with_a_negative_segment_hangs_it_below_zero() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("vertical", "normalize");
        let positive = render(&make_payload(payload_json.clone()), &test_fonts()).expect("renders");
        payload_json["rows"][1]["val"] = json!(-4.0);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        assert_ne!(svg, positive, "the negative segment draws a different bar");
    }

    #[test]
    fn vertical_stack_zero_negative_value_hangs_a_segment_below_the_zero_line() {
        // Category "A": x=+10 climbs from the zero line, y=-4 descends from
        // it (Vega's stackZero two-running-sum law). "B" stays all-positive,
        // pinning that ordinary stacking still touches at the (now
        // floor-shifted) zero line rather than the plot's bottom edge.
        use serde_json::json;
        let mut payload_json = make_stacked_payload("vertical", "zero");
        payload_json["rows"] = json!([
            {"cat": "A", "series": "x", "val": 10.0},
            {"cat": "A", "series": "y", "val": -4.0},
            {"cat": "B", "series": "x", "val": 5.0},
            {"cat": "B", "series": "y", "val": 15.0},
        ]);
        payload_json["resolved"]["stacked_domain_max"] = json!(20.0);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("negative stacked value renders");

        let rects = parse_rects(&svg);
        assert_eq!(rects.len(), 4, "2 categories × 2 series, including the negative segment: {rects:?}");

        let cat_a: Vec<_> = rects.iter().filter(|(x, ..)| *x < 40.0).collect();
        assert_eq!(cat_a.len(), 2, "category A has 2 segments: {cat_a:?}");
        let (above, below) = if cat_a[0].1 < cat_a[1].1 { (cat_a[0], cat_a[1]) } else { (cat_a[1], cat_a[0]) };
        assert!(
            (above.1 + above.3 - below.1).abs() < 0.1,
            "the positive segment's bottom touches the negative segment's top at the zero line: {above:?} / {below:?}"
        );
        assert_eq!(above.4, "#3164a3", "positive series x (largest overall sum) receives palette[0]");
        assert_eq!(below.4, "#779bc9", "negative series y receives palette[1]");
    }

    #[test]
    fn horizontal_stack_zero_negative_value_extends_a_segment_left_of_the_zero_line() {
        // Mirrors `vertical_stack_zero_negative_value_hangs_a_segment_below_the_zero_line`
        // for the horizontal arm's own cumulative loop.
        use serde_json::json;
        let mut payload_json = make_stacked_payload("horizontal", "zero");
        payload_json["rows"] = json!([
            {"cat": "A", "series": "x", "val": 10.0},
            {"cat": "A", "series": "y", "val": -4.0},
            {"cat": "B", "series": "x", "val": 5.0},
            {"cat": "B", "series": "y", "val": 15.0},
        ]);
        payload_json["resolved"]["stacked_domain_max"] = json!(20.0);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("negative stacked value renders");

        let rects = parse_rects(&svg);
        assert_eq!(rects.len(), 4, "2 categories × 2 series, including the negative segment: {rects:?}");

        let mut cat_a: Vec<_> = rects.iter().collect();
        cat_a.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap());
        let cat_a = &cat_a[0..2];
        let (right, left) = if cat_a[0].0 > cat_a[1].0 { (cat_a[0], cat_a[1]) } else { (cat_a[1], cat_a[0]) };
        assert!(
            (left.0 + left.2 - right.0).abs() < 0.1,
            "the negative segment's right edge touches the positive segment's left edge at the zero line: {left:?} / {right:?}"
        );
        assert_eq!(right.4, "#3164a3", "positive series x (largest overall sum) receives palette[0]");
        assert_eq!(left.4, "#779bc9", "negative series y receives palette[1]");
    }

    #[test]
    fn vertical_stack_zero_negative_segment_rounds_its_bottom_not_its_top() {
        // The far-end corner radius flips with sign on the stacked cumulative
        // loop too, the same law `horizontal_bar_negative_bar_rounds_its_left_end`
        // pins on the single-series arm: production splits a mixed-sign stack
        // into two VL layers, each with its own tip-corner radius
        // (`cornerRadiusEnd` on the positive layer, `cornerRadiusBottomLeft` +
        // `cornerRadiusBottomRight` on the negative one — probed against
        // playground/chart-lab-import-review's column_difference_review, whose
        // Vega SVG rounds a "below" bar's BOTTOM corners, not its top). One
        // category per sign avoids any same-category stacking order question.
        use serde_json::json;
        let mut payload_json = make_stacked_payload("vertical", "zero");
        payload_json["resolved"]["style"]["mark"]["border"]["radius"] = json!(2.0);
        payload_json["rows"] = json!([
            {"cat": "A", "series": "s", "val": 10.0},
            {"cat": "B", "series": "s", "val": -4.0},
        ]);
        payload_json["resolved"]["stacked_domain_max"] = json!(20.0);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("negative stacked value renders");

        let bar_paths: Vec<&str> = svg
            .match_indices("<path d=\"M ")
            .map(|(i, _)| svg[i..].split('>').next().unwrap())
            .filter(|p| p.contains(" A "))
            .collect();
        assert_eq!(bar_paths.len(), 2, "positive and negative segments both draw rounded: {bar_paths:?}");

        // Positive segment (round-top, sweep=1 — convex bump on the far/top end).
        assert!(
            bar_paths[0].contains(" 0 0,1 "),
            "positive segment rounds its top (far) end, sweep=1: {}",
            bar_paths[0]
        );
        // Negative segment (round-bottom, sweep=0 — the mirrored convex bump).
        assert!(
            bar_paths[1].contains(" 0 0,0 "),
            "negative segment rounds its bottom (far) end, sweep=0: {}",
            bar_paths[1]
        );
    }

    #[test]
    fn horizontal_stack_zero_negative_segment_rounds_its_left_not_its_right() {
        // Mirrors `vertical_stack_zero_negative_segment_rounds_its_bottom_not_its_top`
        // for the horizontal arm's stacked cumulative loop — the same
        // `bar_passes` sign-aware rounding the single-series arm already
        // carries (`horizontal_bar_negative_bar_rounds_its_left_end`), now
        // exercised through the stacked loop instead of the hardcoded
        // positive-end rounding it had.
        use serde_json::json;
        let mut payload_json = make_stacked_payload("horizontal", "zero");
        payload_json["resolved"]["style"]["mark"]["border"]["radius"] = json!(2.0);
        payload_json["rows"] = json!([
            {"cat": "A", "series": "s", "val": 10.0},
            {"cat": "B", "series": "s", "val": -4.0},
        ]);
        payload_json["resolved"]["stacked_domain_max"] = json!(20.0);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("negative stacked value renders");

        let bar_paths: Vec<&str> = svg
            .match_indices("<path d=\"M ")
            .map(|(i, _)| svg[i..].split('>').next().unwrap())
            .filter(|p| p.contains(" A "))
            .collect();
        assert_eq!(bar_paths.len(), 2, "positive and negative segments both draw rounded: {bar_paths:?}");

        // Positive bar (round-right): "M x,y H ... A" — the H command comes
        // before the first arc (see `horizontal_bar_negative_bar_rounds_its_left_end`).
        let positive_second_cmd = bar_paths[0].split(' ').nth(3).unwrap();
        assert_eq!(positive_second_cmd, "H", "positive segment rounds its right (far) end: {}", bar_paths[0]);
        // Negative bar (round-left): "M x,y A ..." — the arc comes first.
        let negative_second_cmd = bar_paths[1].split(' ').nth(3).unwrap();
        assert_eq!(negative_second_cmd, "A", "negative segment rounds its left (far) end: {}", bar_paths[1]);
    }

    /// A category whose every value is 0 has no share: its bars have no extent
    /// (Vega's 0/0), while a sibling category still draws.
    #[test]
    fn normalize_stack_zero_sum_category_draws_no_extent() {
        use serde_json::json;
        let mut payload_json = make_stacked_payload("vertical", "normalize");
        payload_json["rows"][0]["val"] = json!(0.0);
        payload_json["rows"][1]["val"] = json!(0.0);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        assert!(svg.contains("NaN"), "the empty category's segments have no extent: {svg}");
        assert!(svg.contains("100%"), "{svg}");
    }

    /// Production splits a normalized mixed-sign column into a `>= 0` layer and a
    /// `< 0` one, each stacked up from the zero rule over its own magnitudes: the
    /// lone positive of a column that also holds a negative spans the whole share.
    #[test]
    fn normalize_stack_gives_each_sign_layer_the_whole_share() {
        use serde_json::json;
        let first_bar_top = |second: f64| {
            let mut payload_json = make_stacked_payload("vertical", "normalize");
            payload_json["rows"][1]["val"] = json!(second);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            let rect = svg.split("<rect x=\"").nth(1).expect("a bar rect");
            rect.split("y=\"").nth(1).and_then(|rest| rest.split('"').next()).expect("a top edge").parse::<f64>().unwrap()
        };
        let stacked = first_bar_top(30.0);
        let layered = first_bar_top(-30.0);
        assert!(layered < stacked, "a lone positive layer reaches the top: {layered} vs {stacked}");
    }

    /// `1` and `'1'` pass `ERR-BAR-DUPLICATE-ROWS` (typed keys), read as one category, and stack.
    #[test]
    fn rows_whose_category_reads_alike_stack_over_each_other() {
        let mut payload_json = make_stacked_payload("vertical", "zero");
        let key = payload_json["resolved"]["x"].as_str().unwrap().to_string();
        let mut dup = payload_json["rows"][0].clone();
        payload_json["rows"][0][&key] = serde_json::json!(1);
        dup[&key] = serde_json::json!("1");
        payload_json["rows"].as_array_mut().unwrap().push(dup);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        assert_eq!(svg.matches("<rect x=").count(), 5, "the repeated row paints its own bar: {svg}");
    }

    // ── Axis titles ───────────────────────────────────────────────────────────

    fn enable_axis_title(axis: &mut serde_json::Value) {
        axis["title"] = serde_json::json!({
            "font": {"family": TEST_FONT, "size": 11.0, "weight": "600", "color": "#8A8C8F"},
            "padding": 10.0,
            "visible": true
        });
    }

    #[test]
    fn horizontal_bar_draws_rotated_category_title_left_of_labels() {
        let mut payload_json =
            make_vertical_payload(&[("Alpha", 1800.0), ("Beta", 1200.0)], false);
        payload_json["resolved"]["orientation"] = serde_json::json!("horizontal");
        enable_axis_title(&mut payload_json["resolved"]["style"]["axis_x"]);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        // Default title text is the bare field name (production's
        // slug_to_text — no title-casing, no font.case), never the field
        // slug title-cased.
        assert!(svg.contains(">Product<"), "category title text present: {svg}");
        assert!(
            svg.contains("rotate(-90"),
            "category title rotated -90 on the left: {svg}"
        );

        // Deleting the title-band reservation would collapse left_margin back
        // onto the label-only width — pin that plot_left grew past it.
        let no_title_json = {
            let mut j = make_vertical_payload(&[("Alpha", 1800.0), ("Beta", 1200.0)], false);
            j["resolved"]["orientation"] = serde_json::json!("horizontal");
            j
        };
        let no_title_svg = render(&make_payload(no_title_json), &fonts).expect("renders");
        let plot_left = |svg: &str| -> f64 {
            let idx = svg.find("<g transform=\"translate(").expect("plot group");
            svg[idx..idx + 60]
                .split("translate(")
                .nth(1)
                .unwrap()
                .split(',')
                .next()
                .unwrap()
                .parse()
                .unwrap()
        };
        assert!(
            plot_left(&svg) > plot_left(&no_title_svg) + 15.0,
            "titled plot_left={} should clear the untitled plot_left={} by roughly \
             the title band (font.size + padding = 21)",
            plot_left(&svg),
            plot_left(&no_title_svg)
        );
    }

    #[test]
    fn horizontal_bar_draws_bottom_value_title_below_tick_labels() {
        let mut payload_json =
            make_vertical_payload(&[("Alpha", 1800.0), ("Beta", 1200.0)], false);
        payload_json["resolved"]["orientation"] = serde_json::json!("horizontal");
        enable_axis_title(&mut payload_json["resolved"]["style"]["axis_y"]);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        assert!(svg.contains(">Revenue<"), "value title text present: {svg}");

        // The title text must sit below every value tick-label's y — deleting
        // the "+ label_font_size + title.padding" offset would draw it on
        // top of (or above) the tick-label row instead.
        let title_idx = svg.find(">Revenue<").unwrap();
        let title_tag_start = svg[..title_idx].rfind("<text").unwrap();
        let title_y: f64 = svg[title_tag_start..title_idx]
            .split("y=\"")
            .nth(1)
            .unwrap()
            .split('"')
            .next()
            .unwrap()
            .parse()
            .unwrap();

        let tick_label_ys: Vec<f64> = svg
            .match_indices("text-anchor=\"middle\"")
            .filter_map(|(pos, _)| {
                let tag_start = svg[..pos].rfind("<text")?;
                let tag_end = svg[pos..].find('>').map(|e| pos + e)?;
                let content_end = svg[tag_end..].find("</text>")? + tag_end;
                let content = &svg[tag_end + 1..content_end];
                if content == "Revenue" {
                    return None; // exclude the axis title itself
                }
                let tag = &svg[tag_start..pos];
                tag.split("y=\"").nth(1)?.split('"').next()?.parse().ok()
            })
            .collect();
        let max_tick_y = tick_label_ys
            .into_iter()
            .fold(None, |acc: Option<f64>, y| Some(acc.map_or(y, |a: f64| a.max(y))))
            .expect("at least one value tick label");
        assert!(
            title_y > max_tick_y,
            "title_y={title_y} should sit below the lowest tick label y={max_tick_y}"
        );
    }

    /// Regression for `bar.rs`'s horizontal `bottom_margin` reservation
    /// (`val_font_size + val_pad + val_title_band`): the previous test only
    /// checks the title *draws* below the tick labels, which stays true even
    /// if the plot box itself never shrinks to make room — deleting the
    /// `val_title_band` term from `bottom_margin` must fail this test by
    /// shrinking the gap back to zero.
    #[test]
    fn mark_value_labels_draw_at_bar_ends() {
        // charts/bar-charts_9 authors `style.marks.bar.labels` (position:
        // top → inside the bar tip, align right dx −4 per
        // BAR_POS_MAP_HORIZONTAL; VL text default baseline middle). The
        // label paints the formatted measure at bar_end − 4, anchored end.
        let mut j = make_vertical_payload(&[("Alpha", 1800.0), ("Beta", 1200.0)], false);
        j["resolved"]["orientation"] = serde_json::json!("horizontal");
        j["resolved"]["style"]["mark"]["labels"] = serde_json::json!({
            "visible": true,
            "field": null,
            "format": ",.0f",
            "dx": null,
            "dy": null,
            "font": {"family": null, "color": "#6b7280", "size": 11.0},
            "position": "top"
        });
        let svg = render(&make_payload(j), &test_fonts()).expect("renders");
        let i = svg.find(">1,800<").expect("formatted label present");
        let tag = &svg[svg[..i].rfind("<text").unwrap()..i];
        assert!(tag.contains("text-anchor=\"end\""), "top position anchors end: {tag}");
        assert!(tag.contains("#6b7280"), "authored label color: {tag}");
        // Coordinate pin: the label sits 4px inside the widest bar's end.
        let label_x: f64 =
            tag.split("x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
        let plot_left: f64 = {
            let g = svg.find("<g transform=\"translate(").unwrap();
            svg[g..].split("translate(").nth(1).unwrap().split(',').next().unwrap().parse().unwrap()
        };
        let bar_end: f64 = {
            // First rounded-corner arc target of the widest bar's path is
            // the bar's true end x (`A r,r 0 0,1 {x2},{y+r}`).
            let b = svg.find("<path d=\"M 0.00").expect("widest bar path");
            svg[b..].split("0,1 ").nth(1).unwrap().split(',').next().unwrap().parse().unwrap()
        };
        assert!(
            (label_x - (plot_left + bar_end - 4.0)).abs() < 0.01,
            "top label at bar_end − 4: label_x={label_x} plot_left={plot_left} bar_end={bar_end}"
        );
        // Vertical: default position "above" sits over the column top.
        let mut j = make_vertical_payload(&[("Alpha", 1800.0), ("Beta", 1200.0)], false);
        j["resolved"]["style"]["mark"]["labels"] = serde_json::json!({
            "visible": true,
            "field": null,
            "format": ",.0f",
            "dx": null,
            "dy": null,
            "font": {"family": null, "color": "#6b7280", "size": 11.0},
            "position": null
        });
        let svg = render(&make_payload(j), &test_fonts()).expect("renders");
        assert!(svg.contains(">1,800<"), "vertical above label present");
    }

    /// Front-end contract: a labeled bar's value labels are ONE text mark set
    /// stamped `data-dbt-value-label` (`converters/chart.py::
    /// _stamp_value_label_layers`), each label carrying its datum's aria-label
    /// byte-identical to its bar's -- `chart_interactivity.js`'s
    /// `recedableMarks` joins a label to its bar on that string alone. The
    /// group's layer ordinal counts the layers before it: the bars, then the
    /// zero rule when one is painted.
    #[test]
    fn value_labels_are_one_stamped_text_group_twinned_to_their_bars() {
        use serde_json::json;
        for horizontal in [false, true] {
            for (rule_painted, layer) in [(true, 2), (false, 1)] {
                let mut j = make_vertical_payload(&[("Alpha", 1800.0), ("Beta", 1200.0)], false);
                if horizontal {
                    j["resolved"]["orientation"] = json!("horizontal");
                }
                j["resolved"]["style"]["tooltip_format"] = json!(",.0f");
                if !rule_painted {
                    j["resolved"]["style"]["axis_y"]["grid"]["threshold"] = json!(null);
                }
                j["resolved"]["style"]["mark"]["labels"] = json!({
                    "visible": true, "field": null, "format": ",.0f", "dx": null,
                    "dy": null, "font": null, "position": null
                });
                let svg = render(&make_payload(j), &test_fonts()).expect("renders");
                let ctx = format!("horizontal={horizontal} rule={rule_painted}\n{svg}");
                let group = format!(
                    "<g class=\"mark-text role-mark layer_{layer}_marks\" \
                     data-dbt-value-label=\"true\" role=\"graphics-object\" \
                     aria-roledescription=\"text mark container\">"
                );
                assert_eq!(svg.matches(&group).count(), 1, "one stamped group: {ctx}");
                assert_eq!(svg.matches("data-dbt-value-label").count(), 1, "{ctx}");
                let group_at = svg.find(&group).unwrap();
                let body = &svg[group_at..group_at + svg[group_at..].find("</g>").unwrap()];
                assert_eq!(body.matches("<text ").count(), 2, "both labels inside it: {ctx}");
                for (cat, shown) in [("Alpha", "1,800"), ("Beta", "1,200")] {
                    let aria = format!("aria-label=\"\u{2061}{cat}; Revenue: {shown}\"");
                    let twin = format!(
                        "{aria} role=\"graphics-symbol\" aria-roledescription=\"text mark\""
                    );
                    assert_eq!(body.matches(&twin).count(), 1, "{cat} label: {ctx}");
                    assert_eq!(
                        svg.matches(&format!("{aria} role=\"graphics-symbol\" aria-roledescription=\"bar\""))
                            .count(),
                        1,
                        "{cat} bar carries the byte-identical label: {ctx}"
                    );
                }
            }
        }
    }

    /// No `tooltip_format` wires no aria-label on the bars, so the labels
    /// stay bare `<text>` outside any group -- the same `tooltip_labels`
    /// gate the bars use.
    #[test]
    fn value_labels_without_a_tooltip_format_stay_unwrapped() {
        let mut j = make_vertical_payload(&[("Alpha", 1800.0)], false);
        j["resolved"]["style"]["mark"]["labels"] = serde_json::json!({
            "visible": true, "field": null, "format": ",.0f", "dx": null,
            "dy": null, "font": null, "position": null
        });
        let svg = render(&make_payload(j), &test_fonts()).expect("renders");
        assert!(svg.contains(">1,800<"));
        assert!(!svg.contains("data-dbt-value-label"), "{svg}");
        assert!(crate::contract::has_no_mark_labels(&svg), "{svg}");
    }

    /// `_build_bar_text_layer`'s non-stacked `middle` branch: the label
    /// paints the REAL value as text, but positioned at HALF the value's own
    /// pixel (`calculate: datum[y_field] / 2`), and — `_INSIDE_BAR_POSITIONS`
    /// — in the background color rather than the plot's own default text
    /// color, since it paints over the bar's own ink.
    #[test]
    fn mark_value_labels_middle_position_uses_half_value_and_background_color() {
        let mut j = make_vertical_payload(&[("Alpha", 1800.0)], false);
        j["resolved"]["style"]["mark"]["labels"] = serde_json::json!({
            "visible": true, "position": "middle",
            "field": null, "format": null, "dx": null, "dy": null, "font": null
        });
        let svg = render(&make_payload(j), &test_fonts()).expect("renders");
        assert!(svg.contains(">1800<"), "shows the real value: {svg}");
        assert!(!svg.contains(">900<"), "does not show the halved value as text: {svg}");
        let i = svg.find(">1800<").expect("label present");
        let tag = &svg[svg[..i].rfind("<text").unwrap()..i];
        assert!(tag.contains("fill=\"#ffffff\""), "middle label paints in the background color: {tag}");
    }

    /// The canvas rect reads the chart's OWN effective `background`
    /// (`BarResolved::background` — chart-local `style.background` when
    /// authored, else the board default, possibly `transparent`), never the
    /// board-level `Payload.background` — production's VL spec background is
    /// `chart.background` (`render/chart/session.py:184`,
    /// `presentation.py:74`). The inside-bar value label's default fill reads
    /// the chart's opaque `canvas` (`BarResolved::canvas`):
    /// `_build_bar_text_layer`'s `_INSIDE_BAR_POSITIONS` default color is
    /// `chart.canvas` (`features/value_labels.py:1572`).
    #[test]
    fn canvas_rect_reads_the_background_and_inside_bar_label_the_canvas() {
        let mut j = make_vertical_payload(&[("Alpha", 1800.0)], false);
        j["resolved"]["style"]["mark"]["labels"] = serde_json::json!({
            "visible": true, "position": "middle",
            "field": null, "format": null, "dx": null, "dy": null, "font": null
        });
        j["resolved"]["background"] = serde_json::json!("transparent");
        j["resolved"]["canvas"] = serde_json::json!("#d9a45b");
        j["background"] = serde_json::json!("#ffffff");
        let svg = render(&make_payload(j), &test_fonts()).expect("renders");
        assert!(svg.contains("<rect width=\"800\" height=\"500\" fill=\"transparent\"/>"), "{svg}");
        let i = svg.find(">1800<").expect("label present");
        let tag = &svg[svg[..i].rfind("<text").unwrap()..i];
        assert!(tag.contains("fill=\"#d9a45b\""), "middle label paints the chart's own canvas: {tag}");
    }

    #[test]
    fn scale_nice_rounds_value_domain_to_tick_step() {
        // charts/bar-charts_6 authors `axis_y.scale.nice: true`: production
        // forwards it as VL `scale.nice`, and Vega then nices the
        // headroomed max AFTER the domainMax override ([0, 81436.32] →
        // [0, 90000]), dropping the baked 100k tick past the new top.
        let j = make_vertical_payload(&[("A", 75404.0)], false);
        let mut ax: AxisStyle = Deserialize::deserialize(&j["resolved"]["style"]["axis_y"]).unwrap();
        ax.scale = serde_json::json!({"nice": true, "headroom": 0.08});
        ax.domain_max = None;
        ax.tick_values = vec![0.0, 20000.0, 40000.0, 60000.0, 80000.0, 100000.0];
        let rows: Vec<serde_json::Map<String, Value>> =
            vec![serde_json::from_value(serde_json::json!({"revenue": 75404.0})).unwrap()];
        let (_dmin, dmax, ticks) =
            value_domain_and_ticks(&rows, "revenue", "revenue", &ax, "none", None, true, None, false, false).unwrap();
        assert_eq!(dmax, 90000.0);
        assert_eq!(ticks.last(), Some(&80000.0));
    }

    fn baked_floor_axis(tick_values: Vec<f64>, domain_min: f64, domain_max: Option<f64>) -> AxisStyle {
        let j = make_vertical_payload(&[("A", 1.0)], false);
        let mut ax: AxisStyle = Deserialize::deserialize(&j["resolved"]["style"]["axis_y"]).unwrap();
        ax.tick_values = tick_values;
        ax.domain_min = Some(domain_min);
        ax.domain_max = domain_max;
        ax
    }

    fn revenue_rows(values: &[f64]) -> Vec<serde_json::Map<String, Value>> {
        values
            .iter()
            .map(|v| serde_json::from_value(serde_json::json!({"revenue": v})).unwrap())
            .collect()
    }

    /// An all-negative zero-anchored column axis bakes its headroom-expanded
    /// floor and no top (`_resolve_cartesian_ticks`): `zero: true` ends the
    /// domain at 0 and the ladder's bottom rung is not the floor
    /// (`anchored_negative`: domainMin −54000 over a −50000 rung). A
    /// horizontal bar refuses: its hanging arm is unprobed.
    #[test]
    fn an_all_negative_baked_floor_spans_the_floor_to_the_zero_rule() {
        let ax = baked_floor_axis(vec![-50000.0, -40000.0, -30000.0, -20000.0, -10000.0, 0.0], -54000.0, None);
        let rows = revenue_rows(&[-50000.0, -40000.0, -30000.0, -45000.0]);
        let (floor, top, ticks) =
            value_domain_and_ticks(&rows, "revenue", "revenue", &ax, "none", None, true, None, false, false).unwrap();
        assert_eq!((floor, top), (-54000.0, 0.0));
        assert_eq!(ticks, [-50000.0, -40000.0, -30000.0, -20000.0, -10000.0, 0.0]);
        let horizontal = value_domain_and_ticks(&rows, "revenue", "revenue", &ax, "none", None, false, None, false, false).unwrap();
        assert_eq!((horizontal.0, horizontal.1), (-54000.0, 0.0));
    }

    /// A mixed-sign axis whose format-floored ladder rounded its bottom rung
    /// below the data bakes the real floor, which `apply_domain_headroom_bounds`
    /// lays over the ladder-derived `domainMin` (`emitters/bar.py:1204`): the
    /// rung below it is not drawn.
    #[test]
    fn a_baked_floor_overrides_the_ladders_bottom_rung() {
        let ax = baked_floor_axis(vec![-50.0, 0.0, 50.0, 100.0], -43.2, Some(129.6));
        let rows = revenue_rows(&[-40.0, 120.0]);
        let (floor, top, ticks) =
            value_domain_and_ticks(&rows, "revenue", "revenue", &ax, "none", None, true, None, false, false).unwrap();
        assert_eq!((floor, top), (-43.2, 129.6));
        assert_eq!(ticks, [0.0, 50.0, 100.0]);
    }

    /// Vertical's own unauthored, unbaked case (`multiples.scale:
    /// "independent"`'s per-panel ladder — no `scale.nice`, no
    /// `tick_values`, no `domain_max`) must NOT nice —
    /// `emitters/bar.py:1083-1096`'s `bar_zero` branch emits `{"nice":
    /// False, "zero": True}` whenever `zero_anchor_pinned_floor` finds no
    /// rung. West's own real data (`fixtures/multiples/05-independent-
    /// scale`) maxes at 210,000: niced, the domain would become
    /// `[0, 220000]` (`d3_nice_domain` rounds to the next 10-count step)
    /// and West's Mar bar would stop 8px short of `plot_top`; un-niced,
    /// the domain stays the raw `[0, 210000]` and Mar's bar reaches the
    /// top exactly.
    #[test]
    fn unbaked_vertical_domain_stays_raw_not_niced_west_reaches_210k() {
        let j = make_vertical_payload(&[("Jan", 90000.0), ("Feb", 150000.0), ("Mar", 210000.0)], false);
        let mut ax: AxisStyle = Deserialize::deserialize(&j["resolved"]["style"]["axis_y"]).unwrap();
        ax.scale = serde_json::json!({});
        ax.domain_max = None;
        ax.tick_values = vec![];
        let rows: Vec<serde_json::Map<String, Value>> = serde_json::from_value(serde_json::json!([
            {"revenue": 90000.0},
            {"revenue": 150000.0},
            {"revenue": 210000.0},
        ]))
        .unwrap();
        let (_dmin, dmax, _ticks) =
            value_domain_and_ticks(&rows, "revenue", "revenue", &ax, "none", None, true, None, false, false).unwrap();
        assert_eq!(dmax, 210000.0, "un-niced: the raw max, not d3_nice_domain's 220000");
    }

    #[test]
    fn stacked_domain_floor_is_the_raw_negative_extent_when_not_zero_anchored() {
        // Vega's stackZero (vega-transforms/Stack.js) per-category running
        // sums: category "A"'s negative total is -15, "B"'s is -10 — the
        // floor is the MORE negative of the two, raw (no headroom, no
        // nice — production emits neither for this row). A baked rung
        // below that floor (-20) must not survive the ladder filter.
        use serde_json::json;
        let j = make_stacked_payload("vertical", "zero");
        let mut ax: AxisStyle = Deserialize::deserialize(&j["resolved"]["style"]["axis_y"]).unwrap();
        ax.tick_values = vec![-20.0, 0.0, 25.0, 50.0];
        let rows: Vec<serde_json::Map<String, Value>> = serde_json::from_value(json!([
            {"cat": "A", "val": -15.0},
            {"cat": "A", "val": 4.0},
            {"cat": "B", "val": 5.0},
            {"cat": "B", "val": -10.0},
        ]))
        .unwrap();
        let (dmin, dmax, ticks) =
            value_domain_and_ticks(&rows, "cat", "val", &ax, "zero", Some(50.0), true, None, false, false).unwrap();
        assert_eq!(dmin, -15.0, "floor is the most negative per-category running sum, raw");
        assert_eq!(dmax, 50.0);
        assert_eq!(ticks, vec![0.0, 25.0, 50.0], "the -20 rung sits below the floor and is dropped");
    }

    #[test]
    fn stacked_domain_floor_keeps_the_first_rung_pin_when_zero_anchored() {
        // scale.continuous.zero: true keeps the EXISTING zero-anchored law
        // (the computed ladder's first rung) instead of the raw negative
        // extent — same negative data as above, an anchored axis.
        use serde_json::json;
        let j = make_stacked_payload("vertical", "zero");
        let mut ax: AxisStyle = Deserialize::deserialize(&j["resolved"]["style"]["axis_y"]).unwrap();
        ax.tick_values = vec![-20.0, 0.0, 25.0, 50.0];
        ax.scale = json!({"continuous": {"zero": true}});
        let rows: Vec<serde_json::Map<String, Value>> = serde_json::from_value(json!([
            {"cat": "A", "val": -15.0},
            {"cat": "A", "val": 4.0},
            {"cat": "B", "val": 5.0},
            {"cat": "B", "val": -10.0},
        ]))
        .unwrap();
        let (dmin, dmax, ticks) =
            value_domain_and_ticks(&rows, "cat", "val", &ax, "zero", Some(50.0), true, None, false, false).unwrap();
        assert_eq!(dmin, -20.0, "zero-anchored keeps the ladder's own first rung, not the raw extent");
        assert_eq!(ticks, vec![-20.0, 0.0, 25.0, 50.0]);
        assert_eq!(dmax, 50.0);
    }

    /// A zero-anchored stacked axis keeps the ladder's first rung as its
    /// floor (the test above) — but when that rung sits ABOVE a category's
    /// negative running sum, the segment would clamp to the baseline and
    /// vanish under the `< 0.5` guard: a chart of different data, drawn
    /// confidently. Refuse by name, on both arms.
    #[test]
    fn zero_anchored_stacked_bar_with_a_negative_segment_below_the_ladder_floor_refuses() {
        use serde_json::json;
        for orientation in ["vertical", "horizontal"] {
            let mut payload_json = make_stacked_payload(orientation, "zero");
            payload_json["resolved"]["style"]["axis_y"]["scale"] = json!({"continuous": {"zero": true}});
            payload_json["resolved"]["style"]["axis_y"]["tick_values"] = json!([0.0, 10.0, 20.0]);
            payload_json["rows"][1]["val"] = json!(-4.0);
            payload_json["resolved"]["stacked_domain_max"] = json!(20.0);
            let payload = make_payload(payload_json);
            let fonts = test_fonts();
            let err = render(&payload, &fonts)
                .expect_err("a negative segment below the anchored ladder floor refuses");
            assert!(
                err.contains("negative") && err.contains("-4"),
                "{orientation}: error names the negative segment: {err}"
            );
        }
    }

    /// An UNBAKED stacked axis (no computed ladder) has no rung to pin a floor
    /// from, so Vega's `stackZero` extent decides it: the floor is the most
    /// negative running sum, raw, and the rungs are d3 ticks inside
    /// `[floor, stacked_domain_max]` — not a ladder seeded from 0.
    #[test]
    fn unbaked_stacked_axis_floors_at_the_negative_running_sum() {
        use serde_json::json;
        let j = make_stacked_payload("vertical", "zero");
        let mut ax: AxisStyle = Deserialize::deserialize(&j["resolved"]["style"]["axis_y"]).unwrap();
        ax.tick_values = vec![];
        let rows: Vec<serde_json::Map<String, Value>> = serde_json::from_value(json!([
            {"cat": "A", "val": -15.0},
            {"cat": "A", "val": 4.0},
        ]))
        .unwrap();
        let (lo, hi, ticks) = value_domain_and_ticks(&rows, "cat", "val", &ax, "zero", Some(4.0), true, None, false, false)
            .expect("an unbaked stack with a negative running sum draws");
        assert_eq!((lo, hi), (-15.0, 4.0));
        // The fixture's authored `ticks.count` of 4 over [-15, 4].
        assert_eq!(ticks, [-15.0, -10.0, -5.0, 0.0]);
    }

    /// The endpoint rail's unbaked ladder takes the resolved `tickMinStep` cap
    /// (`tickCount`, vega-scale `ticks.js`) like every other unbaked measure
    /// ladder: an authored count of 12 over the niced [0, 2.2] is capped to
    /// `floor(2.2 / 1) + 1` = 3, so the rungs are whole numbers.
    #[test]
    fn the_rail_ladder_is_capped_by_the_resolved_step() {
        let j = make_stacked_payload("horizontal", "zero");
        let mut ax: AxisStyle = Deserialize::deserialize(&j["resolved"]["style"]["axis_y"]).unwrap();
        ax.tick_values = vec![];
        ax.ticks.count = Some(12.0);
        ax.ticks.step = Some(1.0);
        let (max, ticks) = niced_rail_domain(&ax, 2.16);
        assert_eq!(max, 2.2);
        assert_eq!(ticks, [0.0, 1.0, 2.0]);
    }

    /// The fit closure's tick-ink probe reads the stacked negative floor
    /// (`v_domain_min`), not a hardcoded 0.0: a ladder whose bottom rung
    /// IS the floor sits at the plot's bottom edge in both, so the
    /// negotiated plot height equals an all-positive chart's with the same
    /// four-rung ladder shifted up. With a 0.0 probe the −10 rung's ink
    /// would land at 1.5·ph and force a bottom margin the reference never
    /// reserves.
    #[test]
    fn fit_probe_reads_the_stacked_negative_floor() {
        use serde_json::json;
        let render_plot_h = |rows: serde_json::Value, ticks: serde_json::Value, dmax: f64| {
            let mut payload_json = make_stacked_payload("vertical", "zero");
            payload_json["rows"] = rows;
            payload_json["resolved"]["style"]["axis_y"]["tick_values"] = ticks;
            payload_json["resolved"]["stacked_domain_max"] = json!(dmax);
            let payload = make_payload(payload_json);
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("stacked chart renders");
            let rects = parse_rects(&svg);
            let top = rects.iter().map(|r| r.1).fold(f64::MAX, f64::min);
            let bottom = rects.iter().map(|r| r.1 + r.3).fold(f64::MIN, f64::max);
            (top, bottom - top)
        };
        // Domain [−10, 20]: B's stack tops out at the domain max, A's
        // negative segment bottoms out at the floor — the bars span the
        // whole plot height.
        let (top_neg, plot_h_neg) = render_plot_h(
            json!([
                {"cat": "A", "series": "x", "val": 10.0},
                {"cat": "A", "series": "y", "val": -10.0},
                {"cat": "B", "series": "x", "val": 5.0},
                {"cat": "B", "series": "y", "val": 15.0},
            ]),
            json!([-10.0, 0.0, 10.0, 20.0]),
            20.0,
        );
        // Domain [0, 30], the same ladder shifted up by 10: A's stack spans
        // the whole plot height.
        let (top_pos, plot_h_pos) = render_plot_h(
            json!([
                {"cat": "A", "series": "x", "val": 10.0},
                {"cat": "A", "series": "y", "val": 20.0},
                {"cat": "B", "series": "x", "val": 5.0},
                {"cat": "B", "series": "y", "val": 15.0},
            ]),
            json!([0.0, 10.0, 20.0, 30.0]),
            30.0,
        );
        assert!((top_neg - top_pos).abs() < 0.01, "same top margin: {top_neg} vs {top_pos}");
        assert!(
            (plot_h_neg - plot_h_pos).abs() < 0.01,
            "the negative-floor chart negotiates the same plot height: {plot_h_neg} vs {plot_h_pos}"
        );
    }

    #[test]
    fn zero_rule_requires_visible_grid() {
        // Production's BaselineFeature bails before inserting the zero rule
        // when `axis_y.grid.visible` is false (features/baseline.py's
        // `if not chart.style.axis_y.grid.visible: return`) — a baked zero
        // style alone does not fire it, and the margin ink goes with it
        // (charts/bar-charts_4's category_columns_styled: origin top 44,
        // not 46).
        let mut j = make_vertical_payload(&[("Alpha", 1800.0), ("Beta", 1200.0)], false);
        j["resolved"]["orientation"] = serde_json::json!("horizontal");
        j["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(false);
        let svg = render(&make_payload(j), &test_fonts()).expect("renders");
        assert!(
            !svg.contains("stroke=\"#8A8C8F\""),
            "zero rule drawn despite hidden grid"
        );
    }

    /// B9: production compiles the zero rule (and a normalize stack's 100%
    /// rule) as its OWN Vega-Lite layer emitted AFTER the bar layer —
    /// `column_stacked_100_review`'s spec is `layer[0] = {mark: bar}`,
    /// `layer[1]/[2] = {mark: rule, encoding.y.datum: 0/1}`, and its
    /// reference SVG paints `concat_0_layer_1_marks` after the last
    /// `concat_0_layer_0_marks` rect group. Vega therefore paints the rule
    /// OVER the bars' 1px white stroke; drawing it first leaves a
    /// one-pixel seam along every bar's baseline (pixel probe on that
    /// chart: the reference carries the rule color through raster rows
    /// 720-723, the port showed background at 721-722).
    #[test]
    fn the_zero_rule_paints_over_the_bar_marks() {
        let fonts = test_fonts();
        let mut vertical =
            make_vertical_payload(&[("Alpha", 1800.0), ("Beta", 1200.0)], false);
        vertical["resolved"]["style"]["mark"]["border"]["radius"] = serde_json::json!(0.0);
        let cases = [
            ("vertical", vertical),
            ("horizontal", make_horizontal_payload()),
            ("vertical normalize", make_stacked_payload("vertical", "normalize")),
            ("horizontal stacked", make_stacked_payload("horizontal", "zero")),
        ];
        for (name, mut j) in cases {
            j["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(true);
            j["resolved"]["style"]["axis_y"]["grid"]["threshold"] =
                serde_json::json!({"color": "#8A8C8F", "width": 2.0});
            let svg = render(&make_payload(j), &fonts).expect("renders");
            let last_bar =
                svg.rfind("<rect x=").unwrap_or_else(|| panic!("{name}: no bar rects: {svg}"));
            let first_rule = svg
                .find("stroke=\"#8A8C8F\"")
                .unwrap_or_else(|| panic!("{name}: no zero rule: {svg}"));
            assert!(
                first_rule > last_bar,
                "{name}: zero rule at {first_rule} is painted under the bars \
                 (last bar rect at {last_bar})"
            );
        }
    }

    /// Regression (#8343 rename): production emits the axis grid's baseline
    /// style under `threshold`, not `zero` — a payload built the way
    /// production actually serializes it must still draw the rule.
    #[test]
    fn zero_rule_reads_the_production_threshold_key() {
        let mut j = make_vertical_payload(&[("Alpha", 1800.0), ("Beta", 1200.0)], false);
        j["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(true);
        j["resolved"]["style"]["axis_y"]["grid"]["threshold"] =
            serde_json::json!({"visible": true, "color": "#8A8C8F", "width": 2.0});
        let svg = render(&make_payload(j), &test_fonts()).expect("renders");
        assert!(
            svg.contains("stroke=\"#8A8C8F\""),
            "zero rule missing when the grid style rides the production `threshold` key"
        );
    }

    /// Regression (#8343 generalize): `grid.threshold.visible` is an
    /// independent off-switch from `grid.visible` — production silences the
    /// rule without also hiding the axis's other gridlines.
    #[test]
    fn threshold_visible_false_suppresses_the_zero_rule_even_with_grid_visible() {
        let mut j = make_vertical_payload(&[("Alpha", 1800.0), ("Beta", 1200.0)], false);
        j["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(true);
        j["resolved"]["style"]["axis_y"]["grid"]["threshold"] =
            serde_json::json!({"visible": false, "color": "#8A8C8F", "width": 2.0});
        let svg = render(&make_payload(j), &test_fonts()).expect("renders");
        assert!(
            !svg.contains("stroke=\"#8A8C8F\""),
            "zero rule drawn despite threshold.visible: false"
        );
    }

    /// Regression: a normalize stack's 0%/100% top rules must not bypass
    /// `grid.visible` — production's `_insert_top_rules` gates normalize
    /// exactly like every other rule: `if not chart.style.axis_y.grid.visible
    /// or not axis_style.grid.threshold.visible: return` (`baseline.py`).
    /// A payload with `grid.visible: false` and a theme-baked
    /// `threshold.visible: true` must draw neither rule, on both
    /// orientations.
    #[test]
    fn normalize_top_rules_require_visible_grid() {
        for orientation in ["vertical", "horizontal"] {
            let mut j = make_stacked_payload(orientation, "normalize");
            j["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(false);
            j["resolved"]["style"]["axis_y"]["grid"]["threshold"] =
                serde_json::json!({"visible": true, "color": "#8A8C8F", "width": 2.0});
            let svg = render(&make_payload(j), &test_fonts()).expect("renders");
            assert!(
                !svg.contains("stroke=\"#8A8C8F\""),
                "{orientation}: normalize top rules drawn despite grid.visible: false"
            );
        }
    }

    /// A normalize stack's reference rules are hidden from screen readers
    /// under any axis format, the 100% rule included.
    #[test]
    fn a_normalize_stacks_rules_are_hidden_under_any_axis_format() {
        use serde_json::json;
        let hidden = "aria-hidden=\"true\"><line";
        for orientation in ["vertical", "horizontal"] {
            for format in [json!(null), json!(".0%")] {
                let mut j = make_stacked_payload(orientation, "normalize");
                j["resolved"]["style"]["axis_y"]["grid"]["visible"] = json!(true);
                j["resolved"]["style"]["axis_y"]["grid"]["threshold"] =
                    json!({"visible": true, "color": "#8A8C8F", "width": 2.0});
                j["resolved"]["style"]["tooltip_format"] = json!(".0%");
                j["resolved"]["style"]["axis_y"]["labels"]["format"] = format;
                j["resolved"]["format"] = json!(".0%");
                let svg = render(&make_payload(j), &test_fonts()).expect("renders");
                let ctx = format!("{orientation}\n{svg}");
                let grids = svg.matches("role-axis-grid").count();
                assert_eq!(svg.matches(hidden).count() - grids, 2, "{ctx}");
                assert_eq!(svg.matches("aria-roledescription=\"rule mark\"").count(), 0, "{ctx}");
            }
        }
    }

    /// A bar payload whose measure axis wears `.0%`: its rows rescaled so the
    /// tallest is `peak`, its baked domain `[0, domain_max]` with a quartile
    /// ladder, a visible grid and a threshold `threshold_w` wide.
    fn percent_axis(
        mut j: serde_json::Value,
        peak: f64,
        domain_max: f64,
        threshold_w: f64,
    ) -> serde_json::Value {
        use serde_json::json;
        let y = j["resolved"]["y"].as_str().expect("measure field").to_string();
        let top = j["rows"]
            .as_array()
            .expect("rows")
            .iter()
            .map(|row| row[&y].as_f64().expect("number"))
            .fold(0.0_f64, f64::max);
        for row in j["rows"].as_array_mut().expect("rows") {
            row[&y] = json!(row[&y].as_f64().expect("number") / top * peak);
        }
        let axis = &mut j["resolved"]["style"]["axis_y"];
        axis["labels"]["format"] = json!(".0%");
        axis["tick_label"] = json!({"format": ".0%"});
        axis["tick_values"] =
            json!([0.0, domain_max / 4.0, domain_max / 2.0, domain_max * 0.75, domain_max]);
        axis["domain_max"] = json!(domain_max);
        axis["grid"]["visible"] = json!(true);
        axis["grid"]["threshold"] = json!({"color": "#8A8C8F", "width": threshold_w});
        j["resolved"]["format"] = json!(".1%");
        j["resolved"]["style"]["tooltip_format"] = json!(".1%");
        j
    }

    /// The `.role-mark` rule groups of a render, in layer order.
    fn rule_groups(svg: &str) -> Vec<&str> {
        svg.split("<g class=\"mark-rule ")
            .skip(1)
            .filter(|g| !g.starts_with("role-axis-grid"))
            .map(|g| &g[..g.find("</g>").unwrap()])
            .collect()
    }

    /// The shapes `_apply_unity` rules at `datum: 1` beyond a normalize stack:
    /// each arm paints the rule as the layer after the zero rule, hidden from
    /// screen readers like every reference rule.
    fn unity_arms() -> Vec<(&'static str, serde_json::Value)> {
        let mut composed = make_vertical_payload(&[("Alpha", 1000.0), ("Beta", 2000.0)], false);
        composed["resolved"]["support_table"] = serde_json::json!({
            "entries": [{
                "source": "revenue",
                "format": {"spec": "$,.0f", "raw": "currency_whole"},
                "label": "Revenue"
            }]
        });
        composed["resolved"]["effective_support_table_style"] = support_table_style_json();
        vec![
            ("vertical fit", make_vertical_payload(&[("Alpha", 1000.0), ("Beta", 2000.0)], false)),
            ("horizontal fit", make_horizontal_payload()),
            ("vertical composed strip", composed),
        ]
    }

    #[test]
    fn a_percent_axis_reaching_100_paints_the_unity_rule_after_the_zero_rule_on_every_arm() {
        for (arm, payload) in unity_arms() {
            let payload = make_payload(percent_axis(payload, 0.9, 1.0, 2.0));
            let svg = render(&payload, &test_fonts()).expect(arm);
            let rules = rule_groups(&svg);
            assert_eq!(rules.len(), 2, "{arm}: zero rule then unity rule\n{svg}");
            let (zero, unity) = (rules[0], rules[1]);
            assert!(zero.contains("layer_1_marks") && zero.contains("aria-hidden=\"true\""), "{arm}: {zero}");
            assert!(unity.contains("layer_2_marks") && unity.contains("aria-hidden=\"true\""), "{arm}: {unity}");
        }
    }

    #[test]
    fn a_percent_axis_stopping_short_of_100_paints_only_the_zero_rule_on_every_arm() {
        for (arm, payload) in unity_arms() {
            let payload = make_payload(percent_axis(payload, 0.5, 0.6, 2.0));
            let svg = render(&payload, &test_fonts()).expect(arm);
            assert_eq!(rule_groups(&svg).len(), 1, "{arm}\n{svg}");
        }
    }

    /// The 100% rule is a stroke of its own at the plot edge, so the fit
    /// reserves its ink like the zero rule's: a wide stroke at the plot top
    /// (vertical) or right edge (horizontal) moves the plot origin down or
    /// pulls the rule in, against the same chart under a hairline stroke.
    #[test]
    fn the_unity_rule_s_stroke_joins_the_fit_s_ink() {
        let render_with = |payload: &serde_json::Value, threshold_w: f64| {
            let payload = percent_axis(payload.clone(), 0.9, 1.0, threshold_w);
            render(&make_payload(payload), &test_fonts()).unwrap()
        };
        let plot_top = |svg: &str| -> f64 {
            let group = svg.split("<g transform=\"translate(").nth(1).unwrap();
            let at = group.split(')').next().unwrap();
            at.split_once(',').unwrap().1.parse().unwrap()
        };
        let rule_x = |svg: &str| -> f64 {
            let rule = rule_groups(svg).last().copied().unwrap();
            rule.split("x1=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        let vertical = make_vertical_payload(&[("Alpha", 1000.0), ("Beta", 2000.0)], false);
        let thin = plot_top(&render_with(&vertical, 2.0));
        let wide = plot_top(&render_with(&vertical, 60.0));
        assert!(wide - thin >= 40.0, "vertical plot top {wide} vs {thin}");
        let horizontal = make_horizontal_payload();
        let thin = rule_x(&render_with(&horizontal, 2.0));
        let wide = rule_x(&render_with(&horizontal, 60.0));
        assert!(thin - wide >= 40.0, "horizontal plot right edge {wide} vs {thin}");
    }

    /// A ranged `make_vertical_payload`: each bar spans `start` to `revenue`.
    fn ranged_payload(zero_anchored: bool) -> serde_json::Value {
        let mut j = make_vertical_payload(&[("Alpha", 1000.0), ("Beta", 2000.0)], false);
        for row in j["rows"].as_array_mut().unwrap() {
            row["start"] = serde_json::json!(100.0);
        }
        j["resolved"]["y_start"] = serde_json::json!("start");
        j["resolved"]["style"]["axis_y"]["zero_anchored"] = serde_json::json!(zero_anchored);
        j
    }

    #[test]
    fn a_percent_axis_reaching_100_draws_the_rule_from_the_pooled_column_on_small_multiples() {
        render(&make_payload(percent_axis(facet::make_facet_payload("shared"), 0.9, 1.0, 2.0)), &test_fonts())
            .expect("renders");
    }

    #[test]
    fn a_right_pinned_layer_leaves_the_100_percent_rule_unpainted() {
        let mut j = make_vertical_payload(&[("Alpha", 1000.0), ("Beta", 2000.0)], false);
        for row in j["rows"].as_array_mut().unwrap() {
            row["target"] = serde_json::json!(5.0);
        }
        let mut layer = line_layer_json("#dea628");
        layer["axis_y"] = serde_json::json!({"position": "right"});
        j["resolved"]["layers"] = serde_json::json!([layer]);
        j["resolved"]["legend"]["visible"] = serde_json::json!(true);
        let svg = render(&make_payload(percent_axis(j, 0.9, 1.0, 2.0)), &test_fonts()).expect("renders");
        assert_eq!(rule_groups(&svg).len(), 1, "only the zero rule: {svg}");
    }

    #[test]
    fn a_percent_axis_reaching_100_paints_the_rule_beside_chart_layers() {
        let mut j = make_vertical_payload(&[("Alpha", 1000.0), ("Beta", 2000.0)], false);
        for row in j["rows"].as_array_mut().unwrap() {
            row["target"] = serde_json::json!(0.5);
        }
        j["resolved"]["layers"] = serde_json::json!([line_layer_json("#dea628")]);
        j["resolved"]["legend"]["visible"] = serde_json::json!(true);
        let svg = render(&make_payload(percent_axis(j, 0.9, 1.0, 2.0)), &test_fonts()).expect("renders");
        let rules = rule_groups(&svg);
        assert_eq!(rules.len(), 2, "{svg}");
        assert!(rules.iter().all(|g| g.contains("aria-hidden")), "{svg}");
    }

    /// A ranged bar off zero has no zero baseline, but `_apply_unity` has no
    /// such gate: the 100% rule is the only rule, and the first layer after
    /// the bars.
    #[test]
    fn a_percent_axis_reaching_100_paints_only_the_rule_on_a_ranged_bar_off_zero() {
        let svg = render(&make_payload(percent_axis(ranged_payload(false), 0.9, 1.0, 2.0)), &test_fonts())
            .expect("renders");
        let rules = rule_groups(&svg);
        assert_eq!(rules.len(), 1, "{svg}");
        assert!(rules[0].contains("layer_1_marks") && rules[0].contains("aria-hidden"), "{}", rules[0]);
    }

    #[test]
    fn a_percent_axis_reaching_100_paints_the_rule_on_a_quantitative_x_bar() {
        let xs: Vec<(f64, f64)> = (0..24).map(|h| (h as f64, 100.0 + h as f64)).collect();
        let svg = render(&make_payload(percent_axis(make_continuous_payload(&xs), 0.9, 1.0, 2.0)), &test_fonts())
            .expect("renders");
        assert!(svg.contains("layer_2_marks"), "{svg}");
    }

    /// A ranged bar whose axis reaches zero keeps its zero rule, so the 100%
    /// rule follows it; both are hidden from screen readers.
    #[test]
    fn a_ranged_bar_on_a_zero_anchored_percent_axis_hides_both_rules() {
        let payload = make_payload(percent_axis(ranged_payload(true), 0.9, 1.0, 2.0));
        let svg = render(&payload, &test_fonts()).expect("renders");
        let rules = rule_groups(&svg);
        assert_eq!(rules.len(), 2, "{svg}");
        assert!(rules.iter().all(|rule| rule.contains("aria-hidden=\"true\"") && !rule.contains("aria-label")), "{svg}");
    }

    /// Reserve-side counterpart of the test above: `baseline_rules` feeds every
    /// margin/rail-layout site that reserves ink for the rule
    /// (`render_horizontal`'s margin and rail reservations, and
    /// `render_vertical`'s shared fit-ink closure). If it disagreed with the
    /// paint gate, the plot rect would over-reserve stroke width for a rule
    /// production never draws — a wrong plot origin with no visible rule to
    /// explain it.
    #[test]
    fn baseline_rules_require_visible_grid_even_when_threshold_is_visible() {
        let mut j = make_stacked_payload("vertical", "normalize");
        j["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(false);
        j["resolved"]["style"]["axis_y"]["grid"]["threshold"] =
            serde_json::json!({"visible": true, "color": "#8A8C8F", "width": 2.0});
        let ax_y: AxisStyle = Deserialize::deserialize(&j["resolved"]["style"]["axis_y"])
            .expect("axis style deserializes");
        assert!(
            baseline_rules(&ax_y, false, true, true).is_none(),
            "reserved ink for a rule production wouldn't draw (grid.visible: false)"
        );
    }

    /// An authored domain that excludes 0 leaves the zero rule no position, so
    /// production adds none (`build_zero_rule_if_applicable`); one that holds 0
    /// at either end keeps it.
    #[test]
    fn the_zero_rule_needs_an_authored_domain_that_holds_zero() {
        let rule_for = |domain: [f64; 2]| {
            let mut j = make_stacked_payload("vertical", "zero");
            j["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(true);
            j["resolved"]["style"]["axis_y"]["grid"]["threshold"] =
                serde_json::json!({"visible": true, "color": "#8A8C8F", "width": 2.0});
            j["resolved"]["style"]["axis_y"]["scale"] = serde_json::json!({"continuous": {"domain": domain}});
            let ax_y: AxisStyle = Deserialize::deserialize(&j["resolved"]["style"]["axis_y"]).unwrap();
            baseline_rules(&ax_y, false, false, true).is_some_and(|rules| rules.zero)
        };
        assert!(rule_for([0.0, 100.0]) && rule_for([100.0, 0.0]) && rule_for([-50.0, 50.0]));
        assert!(!rule_for([50.0, 150.0]) && !rule_for([150.0, 50.0]));
    }

    /// B4: Vega puts the title group at the plot origin MINUS the axis's
    /// own UNROUNDED ink extent, while the plot group's translate takes the
    /// ceil'd extent — so the title's x lands in `[padding, padding + 1)`,
    /// never on the padding itself. The rail concat arm subtracted the
    /// ceil'd extent on both sides and drew every title at x = 16.00 where
    /// the references sit at 16.199 (`column_stacked_review`), 16.800
    /// (`column_stacked_100_review`), 16.983 (`hero_bookings`), 16.602
    /// (`hero_queue_age`) and 16.734 (`column_tickets_by_priority_temporal`).
    #[test]
    fn the_rail_arm_hangs_its_title_off_the_unrounded_axis_extent() {
        let fonts = test_fonts();
        let mut payload_json = make_rail_payload();
        payload_json["resolved"]["style"]["axis_y"]["ticks"]["visible"] = serde_json::json!(true);
        let svg = render(&make_payload(payload_json), &fonts).expect("rail bar renders");

        // The axis extent Vega lays the pane out against: the widest value
        // label's ink plus labelPadding and the tick length.
        let mut widest = 0.0_f64;
        for label in ["0", "25", "50"] {
            if svg.contains(&format!(">{label}<")) {
                widest = widest.max(fonts.measure(label, TEST_FONT, 11.0).expect("measures"));
            }
        }
        let chrome = widest + 6.0 + 6.0; // labelPadding + tickSize
        assert!(chrome.fract() > 0.0, "fixture's axis extent is a whole pixel: {chrome}");

        let plot_left = plot_group_left(&svg);
        let title_x = tag_attr(&text_tag(&svg, "Stacked Test"), "x");
        assert_eq!(plot_left, 16.0 + chrome.ceil(), "plot origin: {svg}");
        assert!(
            (title_x - (16.0 + chrome.ceil() - chrome)).abs() < 0.005,
            "title x {title_x} is not the origin minus the unrounded extent {}: {svg}",
            16.0 + chrome.ceil() - chrome
        );
    }

    /// BR1: the rail's x is `ceil(pane0 + right_ink) + spacing` — Vega's
    /// `gridLayout` ceils pane 0's OWN bound and adds `spacing` raw, never
    /// `round(plot_w + right_ink + spacing)` as one sum — and the canvas
    /// (never touched by the old code, which just re-emits the raw
    /// fractional `payload.width`) is left to overshoot the target width
    /// before the SAME layout re-runs on the corrected pane, mirroring
    /// `line.rs`'s own `_correct_concat_overshoot` two-pass structure
    /// rather than inventing a third. `right_ink` is the zero rule's FULL
    /// stroke width (`boundStroke` expands by `strokeWidth`, not half — a
    /// y gridline never counts, so `grid.visible` alone with no `zero`
    /// rule leaves `right_ink` at 0).
    ///
    /// `plot_w`'s own fractional part is `frac(2·w)` (the two-pass algebra
    /// cancels the integer chrome/stroke terms — see BR1's own worksheet),
    /// so a width like 400.4 whose DOUBLED fraction (0.8) sits past the
    /// half-pixel line is NOT discriminating: `ceil` and `round` agree
    /// whenever the fraction they're rounding is already `> 0.5`, for every
    /// stroke and chrome term (round-tripped: the old code's own
    /// `rail_x_ceils_pane0_plus_right_ink_then_adds_spacing_raw` passed
    /// even reverted to `round(plot_w + right_ink + spacing)` on that
    /// width). 400.2 doubles to a fraction of 0.4 — in `(0, 0.5)` — so
    /// `ceil` rounds UP where `round` rounds DOWN, a real 1px split at
    /// every stroke.
    #[test]
    fn rail_x_ceils_pane0_plus_right_ink_then_adds_spacing_raw() {
        let fonts = test_fonts();
        let widest = ["0", "25", "50"]
            .iter()
            .try_fold(0.0_f64, |acc, s| fonts.measure(s, TEST_FONT, 11.0).map(|w| acc.max(w)))
            .unwrap();
        let y_extent = (widest + 6.0 + 6.0).ceil(); // val_pad(6) + y_tick_eff(6)
        let rail_pane_w = crate::families::cartesian::rail_pane_width(
            &["x".to_string(), "y".to_string()],
            "'Inter'",
            11.0,
            "400",
            &fonts,
            400.2,
        )
        .unwrap();
        let (spacing, w, pad_l, pad_r): (f64, f64, f64, f64) = (6.0, 400.2, 16.0, 16.0);

        for stroke in [1.0, 2.0, 4.0] {
            let mut p = make_rail_payload();
            p["width"] = serde_json::json!(w);
            p["resolved"]["style"]["axis_y"]["ticks"]["visible"] = serde_json::json!(true);
            p["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(true);
            p["resolved"]["style"]["axis_y"]["grid"]["threshold"] =
                serde_json::json!({"color": "#918878", "width": stroke});
            let svg = render(&make_payload(p), &fonts).expect("rail bar renders");

            // Two-pass port of Vega's gridLayout, in bar's own chrome terms.
            let uncorrected_x2 = w + stroke;
            let canvas_w_uncorrected =
                (pad_l + pad_r + y_extent + uncorrected_x2.ceil() + spacing + rail_pane_w).ceil();
            let plot_w = 2.0 * w - canvas_w_uncorrected;
            let corrected_x2 = plot_w + stroke;
            let expected_rail_x_rel = corrected_x2.ceil() + spacing;
            let expected_canvas_w =
                canvas_w_uncorrected + corrected_x2.ceil() - uncorrected_x2.ceil();

            let plot_left = plot_group_left(&svg);
            assert_eq!(plot_left, pad_l + y_extent, "stroke {stroke}: plot origin: {svg}");

            let rail_marker = svg.find("<text x=\"0\" y=\"").expect("rail label present");
            let g_start = svg[..rail_marker].rfind("<g transform=\"translate(").unwrap();
            let rail_x: f64 = svg[g_start..]
                .split("translate(")
                .nth(1)
                .unwrap()
                .split(',')
                .next()
                .unwrap()
                .parse()
                .unwrap();
            assert_eq!(
                rail_x,
                plot_left + expected_rail_x_rel,
                "stroke {stroke}: rail x is plot_left + ceil(pane0 + right_ink) + spacing: {svg}"
            );

            let canvas_w: f64 =
                svg.split("width=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
            assert_eq!(canvas_w, expected_canvas_w, "stroke {stroke}: canvas: {svg}");
        }
    }

    /// The plot group's x translate — every fit arm's negotiated `plot_left`.
    fn plot_group_left(svg: &str) -> f64 {
        let i = svg.find("<g transform=\"translate(").expect("plot group");
        svg[i..].split('(').nth(1).unwrap().split(',').next().unwrap().parse().unwrap()
    }

    /// B12: Vega's `tickRound` snaps an axis grid rule to a whole pixel
    /// inside the axis group's crisp +0.5 translate, while the axis LABEL
    /// keeps its exact scale position — `bar_review`'s reference draws its
    /// labels at 201.5 / 283.89 / 366.27 / 448.66 / 531.05 and its grid at
    /// 201.5 / 283.5 / 366.5 / 448.5 / 531.5, i.e. `floor(label) + 0.5`
    /// each. The horizontal arm drew `position + 0.5` — the crisp offset
    /// without the snap.
    #[test]
    fn horizontal_grid_rules_snap_to_a_whole_pixel_labels_do_not() {
        let mut j = make_horizontal_payload();
        // A ladder whose pixel positions are fractional: the discriminating
        // shape (an evenly halved plot lands every rung on a whole pixel).
        j["resolved"]["style"]["axis_y"]["tick_values"] =
            serde_json::json!([0.0, 300.0, 700.0, 1000.0]);
        let svg = render(&make_payload(j), &test_fonts()).expect("renders");

        let grid_xs: Vec<f64> = svg
            .match_indices("stroke=\"#D0D3D9\"")
            .map(|(idx, _)| {
                let tag_start = svg[..idx].rfind("<line").expect("grid stroke inside a line");
                tag_attr(&svg[tag_start..idx], "x1")
            })
            .collect();
        let label_xs: Vec<f64> =
            ["300", "700", "1,000"].map(|t| tag_attr(&text_tag(&svg, t), "x")).to_vec();
        assert_eq!(grid_xs.len(), 4, "one grid rule per rung, tick 0 included: {svg}");
        let grid_xs = &grid_xs[1..];

        // Grid rules are drawn inside the plot group, labels in canvas
        // coordinates — the reference's law reads on the absolute pair.
        let plot_left = plot_group_left(&svg);
        let mut moved = 0;
        for (gx, lx) in grid_xs.iter().map(|g| g + plot_left).zip(label_xs.iter()) {
            assert!(
                (gx - (lx.floor() + 0.5)).abs() < 0.005,
                "grid rule {gx} is not floor({lx}) + 0.5: {svg}"
            );
            if (gx - lx).abs() > 0.005 {
                moved += 1;
            }
        }
        assert!(moved > 0, "fixture's rungs already sit on whole pixels: {svg}");
    }

    /// The snap moves a range-end grid rule OUTWARD, by `1 − frac(plot_w)`
    /// when `frac(plot_w) ≥ 0.5`, so the clip has to allow the whole pixel
    /// the vertical arm already allows. Vega draws that rule outside the
    /// plot: probe (linear x, ticks 0..100 by 25, plot 331.6) puts the grid
    /// at 0/83/166/249/**332**. Card widths reach a fractional plot width
    /// through production's unrounded column division
    /// (`render/column_packer.py`'s `(available − gutters) / count`), and a
    /// rung sits exactly on the domain max on every normalize stack.
    #[test]
    fn the_range_end_grid_rule_survives_the_snap_at_a_fractional_plot_width() {
        let mut j = make_horizontal_payload();
        j["width"] = serde_json::json!(600.7);
        j["resolved"]["style"]["axis_y"]["tick_values"] =
            serde_json::json!([0.0, 250.0, 500.0, 750.0, 1000.0]);
        let svg = render(&make_payload(j), &test_fonts()).expect("renders");

        let plot_left = plot_group_left(&svg);
        // The top rung's own label sits at the plot's right edge + the axis
        // group's crisp 0.5, so it measures `plot_w` independently of the
        // rules under test.
        let plot_w = tag_attr(&text_tag(&svg, "1,000"), "x") - 0.5 - plot_left;
        assert!(
            plot_w.fract() >= 0.5,
            "fixture must land the snap OUTSIDE the plot (plot_w {plot_w})"
        );

        let grid_xs: Vec<f64> = svg
            .match_indices("stroke=\"#D0D3D9\"")
            .map(|(idx, _)| {
                let tag_start = svg[..idx].rfind("<line").expect("grid stroke inside a line");
                tag_attr(&svg[tag_start..idx], "x1")
            })
            .collect();
        assert_eq!(grid_xs.len(), 5, "one rule per rung, tick 0 included: {svg}");
        let grid_xs = &grid_xs[1..];
        assert_eq!(
            grid_xs[3],
            plot_w.round() + 0.5,
            "the range-end rule keeps its snapped position: {svg}"
        );
    }

    #[test]
    fn horizontal_bar_bottom_title_shrinks_plot_height() {
        let plot_h = |svg: &str| -> f64 {
            let idx = svg.find("stroke=\"#8A8C8F\" stroke-width=\"2\"").expect("zero line present");
            let tag_start = svg[..idx].rfind("<line").unwrap();
            svg[tag_start..idx]
                .split("y2=\"")
                .nth(1)
                .unwrap()
                .split('"')
                .next()
                .unwrap()
                .parse()
                .unwrap()
        };

        let mut titled_json =
            make_vertical_payload(&[("Alpha", 1800.0), ("Beta", 1200.0)], false);
        titled_json["resolved"]["orientation"] = serde_json::json!("horizontal");
        enable_axis_title(&mut titled_json["resolved"]["style"]["axis_y"]);
        let fonts = test_fonts();
        let titled_svg = render(&make_payload(titled_json), &fonts).expect("renders");

        let mut untitled_json =
            make_vertical_payload(&[("Alpha", 1800.0), ("Beta", 1200.0)], false);
        untitled_json["resolved"]["orientation"] = serde_json::json!("horizontal");
        let untitled_svg = render(&make_payload(untitled_json), &fonts).expect("renders");

        // val_title_band = title.font.size(11) + title.padding(10) = 21.
        let titled_h = plot_h(&titled_svg);
        let untitled_h = plot_h(&untitled_svg);
        assert!(
            (untitled_h - titled_h - 21.0).abs() < 0.5,
            "titled plot_h={titled_h} should be exactly 21 less than untitled plot_h={untitled_h}"
        );
    }

    #[test]
    fn vertical_bar_draws_bottom_category_title_and_rotated_value_title() {
        let mut payload_json =
            make_vertical_payload(&[("Alpha", 1800.0), ("Beta", 1200.0)], false);
        enable_axis_title(&mut payload_json["resolved"]["style"]["axis_x"]);
        enable_axis_title(&mut payload_json["resolved"]["style"]["axis_y"]);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        assert!(svg.contains(">Product<"), "bottom category title present: {svg}");
        assert!(svg.contains(">Revenue<"), "rotated value title present: {svg}");
        // Default axis_y.position is "left" (val_on_right false) — the title
        // rotates -90, never +90; the right-side sign is pinned separately by
        // `vertical_bar_right_axis_rotates_value_title_the_other_way`.
        assert!(svg.contains("rotate(-90"), "left-axis title must rotate -90: {svg}");
    }

    /// Regression: with two-row temporal category labels, the bottom title
    /// must sit below row two's baseline by the label_font_size + padding
    /// term — not collide with it at row one's baseline. Deleting the
    /// `has_two_row_labels` offset at the emission site (mirroring
    /// line.rs's `x_label_baseline`) must fail this test.
    #[test]
    fn vertical_bar_two_row_temporal_title_sits_below_row_two() {
        use serde_json::json;
        let mut payload_json = make_vertical_payload(
            &[
                ("2024-01-01", 1800.0),
                ("2024-02-01", 1200.0),
                ("2024-03-01", 900.0),
            ],
            false,
        );
        payload_json["resolved"]["x"] = json!("month");
        payload_json["rows"] = json!([
            {"month": "2024-01-01", "revenue": 1800.0},
            {"month": "2024-02-01", "revenue": 1200.0},
            {"month": "2024-03-01", "revenue": 900.0},
        ]);
        enable_axis_title(&mut payload_json["resolved"]["style"]["axis_x"]);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        assert!(svg.contains(">Month<"), "bottom category title present: {svg}");
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

        // cat_font_size=11, title.padding=10 → title baseline = row2_y + 21.
        assert!(
            title_y > row2_y,
            "title_y={title_y} must sit below row2's baseline y={row2_y}"
        );
        assert!(
            (title_y - row2_y - 21.0).abs() < 0.5,
            "title_y={title_y} should be exactly row2_y({row2_y}) + label_font_size(11) \
             + padding(10) = {}",
            row2_y + 21.0
        );
    }

    #[test]
    fn axis_title_authored_label_overrides_default_field_slug() {
        let mut payload_json =
            make_vertical_payload(&[("Alpha", 1800.0), ("Beta", 1200.0)], false);
        payload_json["resolved"]["x_label"] = serde_json::json!("Custom Category");
        enable_axis_title(&mut payload_json["resolved"]["style"]["axis_x"]);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert!(svg.contains(">Custom Category<"), "authored x_label used verbatim: {svg}");
        assert!(!svg.contains(">product<"));
    }

    /// Symmetric to the x_label test above: `r.y_label` overrides the
    /// rotated value title's default field slug the same way `r.x_label`
    /// overrides the bottom category title. Previously untested — deleting
    /// the `r.y_label.as_deref()` argument in `axis_title_text` for the
    /// value title must fail this test.
    #[test]
    fn y_label_authored_override_overrides_default_field_slug() {
        let mut payload_json =
            make_vertical_payload(&[("Alpha", 1800.0), ("Beta", 1200.0)], false);
        payload_json["resolved"]["y_label"] = serde_json::json!("Custom Value");
        enable_axis_title(&mut payload_json["resolved"]["style"]["axis_y"]);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert!(svg.contains(">Custom Value<"), "authored y_label used verbatim: {svg}");
        assert!(!svg.contains(">revenue<"));
    }

    /// Regression for the `val_on_right` branch of the rotated value title:
    /// every other axis-title test uses `make_vertical_payload`'s default
    /// `axis_y.position == "left"`, so this branch — and its rotation sign —
    /// never ran. `+90°` (not `-90°`) and the title sitting to the right of
    /// the label edge (not the left) are both load-bearing: deleting the
    /// `val_on_right` ternaries in `val_label_edge_x`/the rotation sign must
    /// fail this test.
    #[test]
    fn vertical_bar_right_axis_rotates_value_title_the_other_way() {
        let mut payload_json =
            make_vertical_payload(&[("Alpha", 1800.0), ("Beta", 1200.0)], false);
        payload_json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("right");
        enable_axis_title(&mut payload_json["resolved"]["style"]["axis_y"]);
        let payload = make_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        assert!(svg.contains(">Revenue<"), "rotated value title present: {svg}");
        assert!(
            svg.contains("rotate(90 "),
            "right-side axis title must rotate +90 (not -90): {svg}"
        );
        assert!(!svg.contains("rotate(-90"), "right-side axis title must not rotate -90: {svg}");

        // Under the vega-fit layout, right-side value labels are
        // right-aligned to the gutter's far edge (val_label_edge_x ==
        // val_label_x); the title must sit further right still
        // (title_x = val_label_edge_x + padding + nudge), not to the left
        // of it as the left-axis branch would place it.
        let title_pos = svg.find(">Revenue<").unwrap();
        let title_tag_start = svg[..title_pos].rfind("<text").unwrap();
        let title_x: f64 = svg[title_tag_start..title_pos]
            .split("x=\"")
            .nth(1)
            .unwrap()
            .split('"')
            .next()
            .unwrap()
            .parse()
            .unwrap();

        let val_label_pos = svg.find("text-anchor=\"end\"").expect("right-anchored val label");
        let val_label_tag_start = svg[..val_label_pos].rfind("<text").unwrap();
        let val_label_x: f64 = svg[val_label_tag_start..val_label_pos]
            .split("x=\"")
            .nth(1)
            .unwrap()
            .split('"')
            .next()
            .unwrap()
            .parse()
            .unwrap();

        assert!(
            title_x > val_label_x,
            "right-side title_x={title_x} must sit further right than the val label x={val_label_x}"
        );
    }

    mod facet {
        use super::*;
        use serde_json::json;

        /// A rows-only multiples payload: 2 panels (North/South), reusing
        /// `make_vertical_payload`'s own axis/style JSON — `render_facet`'s
        /// own scope gate needs a `multiples`/`panel_axes` pair `regroup`
        /// can replay.
        pub(super) fn make_facet_payload(scale: &str) -> serde_json::Value {
            let mut payload_json =
                make_vertical_payload(&[("Jan", 1200.0), ("Feb", 1800.0), ("Mar", 2400.0)], false);
            payload_json["resolved"]["title"] = json!(null);
            // `make_vertical_payload`'s own base fixture bakes `"left"` —
            // fine for a single chart (`val_on_right` flips symmetrically),
            // but `render_facet`'s own composite math (`primary_reserve`/
            // `composite_axis_reserve`/`ghost_reserve`) hardcodes a
            // RIGHT-side axis throughout, the same law production's own
            // unlayered bar default resolves to (`_overlay.py:633`). A
            // multi-column facet on `"left"` refuses outright (`render_
            // facet`'s own scope gate) — every facet test below exercises
            // the modeled, right-side shape.
            payload_json["resolved"]["style"]["axis_y"]["position"] = json!("right");
            // Production themes bake `ticks.visible: false`; the tick term
            // has its own tests.
            payload_json["resolved"]["style"]["axis_y"]["ticks"]["visible"] = json!(false);
            payload_json["resolved"]["multiples"] =
                json!({"rows": "region", "columns": null, "scale": scale});
            payload_json["resolved"]["panel_axes"] =
                json!([{"field": "region", "values": ["\"North\"", "\"South\""]}]);
            payload_json["rows"] = json!([
                {"product": "Jan", "region": "North", "revenue": 1200.0},
                {"product": "Feb", "region": "North", "revenue": 1800.0},
                {"product": "Mar", "region": "North", "revenue": 2400.0},
                {"product": "Jan", "region": "South", "revenue": 900.0},
                {"product": "Feb", "region": "South", "revenue": 1500.0},
                {"product": "Mar", "region": "South", "revenue": 2100.0},
            ]);
            payload_json["width"] = json!(600.0);
            payload_json["height"] = json!(432.0);
            payload_json["padding"] = json!(null);
            payload_json
        }

        /// A horizontal bar over two panels of the same three categories: the
        /// category axis is the composer's `row_header`, the value axis its
        /// `column_footer`.
        fn horizontal_facet_payload(multiples: serde_json::Value) -> serde_json::Value {
            let mut payload_json = make_horizontal_payload();
            payload_json["resolved"]["multiples"] = multiples;
            payload_json["resolved"]["panel_axes"] =
                json!([{"field": "panel", "values": ["\"A\"", "\"B\""]}]);
            payload_json["rows"] = json!([
                {"product": "North", "panel": "A", "revenue": 800.0},
                {"product": "South", "panel": "A", "revenue": 600.0},
                {"product": "East", "panel": "A", "revenue": 400.0},
                {"product": "North", "panel": "B", "revenue": 700.0},
                {"product": "South", "panel": "B", "revenue": 500.0},
                {"product": "East", "panel": "B", "revenue": 300.0},
            ]);
            payload_json["width"] = json!(600.0);
            payload_json["height"] = json!(432.0);
            payload_json["padding"] = json!(null);
            payload_json
        }

        fn text_count(svg: &str, label: &str) -> usize {
            svg.matches(&format!(">{label}</text>")).count()
        }

        #[test]
        fn a_horizontal_rows_facet_draws_one_category_axis_per_row_and_one_shared_value_axis() {
            let svg = rendered(horizontal_facet_payload(json!({"rows": "panel", "columns": null, "scale": "shared"})));
            assert_eq!(svg.matches("role-scope cell").count(), 1, "{svg}");
            for category in ["North", "South", "East"] {
                assert_eq!(text_count(&svg, category), 2, "{category} once per row header: {svg}");
            }
            for tick in ["0", "500", "1,000"] {
                assert_eq!(text_count(&svg, tick), 1, "tick {tick} once in the shared footer: {svg}");
            }
            assert_eq!((text_count(&svg, "A"), text_count(&svg, "B")), (1, 1), "row titles: {svg}");
        }

        #[test]
        fn a_horizontal_columns_facet_shares_one_category_axis_and_foots_every_column() {
            let svg = rendered(horizontal_facet_payload(json!({"rows": null, "columns": "panel", "scale": "shared"})));
            for category in ["North", "South", "East"] {
                assert_eq!(text_count(&svg, category), 1, "{category} once in the one row header: {svg}");
            }
            assert_eq!(text_count(&svg, "500"), 2, "every column foots its own value axis: {svg}");
        }

        #[test]
        fn a_mirrored_horizontal_facet_repeats_the_category_axis_on_the_right_edge() {
            let mut payload_json = horizontal_facet_payload(json!({"rows": "panel", "columns": null, "scale": "shared"}));
            payload_json["resolved"]["style"]["axis_y"]["mirror"] = json!(true);
            let svg = rendered(payload_json);
            for category in ["North", "South", "East"] {
                assert_eq!(text_count(&svg, category), 4, "{category} on both edges of both rows: {svg}");
            }
            assert!(svg.contains("ghost_marks"), "the mirror's invisible rule layer: {svg}");
        }

        /// A facet repeats a category across its panels, and production's default
        /// horizontal sort folds the repeats with a sum (`vl_sort_op`). The data
        /// ranks differently under sum, min and max.
        #[test]
        fn a_horizontal_facets_categories_rank_by_their_summed_value() {
            let mut payload_json = horizontal_facet_payload(json!({"rows": "panel", "columns": null, "scale": "shared"}));
            payload_json["rows"] = json!([
                {"product": "A", "panel": "A", "revenue": 60.0},
                {"product": "B", "panel": "A", "revenue": 200.0},
                {"product": "C", "panel": "A", "revenue": 70.0},
                {"product": "A", "panel": "B", "revenue": 60.0},
                {"product": "B", "panel": "B", "revenue": -70.0},
                {"product": "C", "panel": "B", "revenue": 45.0},
            ]);
            let svg = rendered(payload_json);
            let at = |label: &str| svg.find(&format!(">{label}</text>")).unwrap();
            assert!(at("B") < at("A") && at("A") < at("C"), "sums 130, 120, 115: {svg}");
        }

        /// The cumulative `translate` x of every `<g>` open at `pos`.
        fn group_x_at(svg: &str, pos: usize) -> f64 {
            let mut stack: Vec<f64> = Vec::new();
            let mut at = 0;
            while let Some(found) = svg[at..pos].find('<') {
                let tag_start = at + found;
                let tag = &svg[tag_start..];
                if tag.starts_with("</g>") {
                    stack.pop();
                } else if tag.starts_with("<g") && tag[..tag.find('>').unwrap()].ends_with('/') == false {
                    let open = &tag[..tag.find('>').unwrap()];
                    let dx = open
                        .split("transform=\"translate(")
                        .nth(1)
                        .map_or(0.0, |t| t.split(',').next().unwrap().parse::<f64>().unwrap());
                    stack.push(dx);
                }
                at = tag_start + 1;
            }
            stack.iter().sum()
        }

        /// The plot's left edge and the right edge of the widest wrapped, start-anchored category
        /// label left of the plot (the `(plot_left, label_right)` of the first bar and of every label with lines).
        fn plot_left_and_label_right(svg: &str, fonts: &measure::FontBook, ink: &str) -> (f64, f64) {
            let plot_left = group_x_at(svg, svg.find("fill=\"#3164a3\"").expect("a bar"));
            let mut right = f64::MIN;
            for (pos, _) in svg.match_indices("<text") {
                let tag = &svg[pos..pos + svg[pos..].find("</text>").unwrap()];
                if !tag.contains("<tspan>") || !tag.contains(&format!("fill=\"{ink}\"")) || !tag.contains("text-anchor=\"start\"") {
                    continue;
                }
                let x: f64 = tag.split(" x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
                let widest = tag
                    .split("<tspan")
                    .skip(1)
                    .map(|t| fonts.measure(t.split_once('>').unwrap().1.split('<').next().unwrap(), TEST_FONT, 11.0).unwrap())
                    .fold(0.0_f64, f64::max);
                if group_x_at(svg, pos) + x < plot_left {
                    right = right.max(group_x_at(svg, pos) + x + widest);
                }
            }
            (plot_left, right)
        }

        fn wrapped_names_payload(faceted: bool) -> serde_json::Value {
            let long = "Europe Middle East and Africa Enterprise Accounts";
            let mut payload_json = horizontal_facet_payload(json!({"rows": "panel", "columns": null, "scale": "shared"}));
            let mut rows = vec![
                json!({"product": long, "panel": "A", "revenue": 60.0}),
                json!({"product": "Short", "panel": "A", "revenue": 200.0}),
            ];
            if faceted {
                rows.push(json!({"product": long, "panel": "B", "revenue": 60.0}));
                rows.push(json!({"product": "Short", "panel": "B", "revenue": 45.0}));
            } else {
                payload_json["resolved"]["multiples"] = json!(null);
                payload_json["resolved"]["panel_axes"] = json!([]);
            }
            payload_json["rows"] = json!(rows);
            payload_json
        }

        /// A faceted row header's gutter fits the labels it paints, as a single chart's does.
        #[test]
        fn a_faceted_wrapped_header_leaves_the_gap_a_single_chart_does() {
            let fonts = test_fonts();
            let gap = |faceted: bool| {
                let svg = crate::text::expand_lines(rendered(wrapped_names_payload(faceted)));
                let (plot_left, label_right) = plot_left_and_label_right(&svg, &fonts, "#626366");
                plot_left - label_right
            };
            let (single, faceted) = (gap(false), gap(true));
            // The composer anchors the row header at the cell's ink edge, which sits up to 2px off
            // the plot; the gutter is otherwise the labels' own width and the label gap.
            assert!((single - faceted).abs() < 2.5, "single {single} vs faceted {faceted}");
            assert!(single < 6.0, "the gap is the label gap, not the one-line gutter: {single}");
        }

        /// A mirrored axis's labels begin a label gap past the plot, as the left labels end one
        /// before it, and stay inside the canvas.
        #[test]
        fn a_mirrored_wrapped_axis_starts_its_right_labels_one_label_gap_past_the_plot() {
            let fonts = test_fonts();
            let mut payload_json = wrapped_names_payload(false);
            payload_json["resolved"]["style"]["axis_y"]["mirror"] = json!(true);
            payload_json["resolved"]["style"]["mark"]["border"]["radius"] = json!(0.0);
            payload_json["rows"][1]["revenue"] = json!(1000.0);
            payload_json["resolved"]["style"]["axis_y"]["domain_max"] = json!(1000.0);
            payload_json["resolved"]["style"]["axis_y"]["tick_values"] = json!([0.0, 500.0, 1000.0]);
            let svg = crate::text::expand_lines(rendered(payload_json));
            let (plot_left, left_right) = plot_left_and_label_right(&svg, &fonts, "#626366");
            let bar = svg.find("fill=\"#3164a3\"").expect("a bar");
            let widest_bar_right = svg
                .match_indices("<rect x=\"0.00\"")
                .map(|(pos, _)| {
                    let tag = &svg[pos..];
                    let width: f64 = tag.split("width=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
                    group_x_at(&svg, pos) + width
                })
                .fold(f64::MIN, f64::max);
            assert!(group_x_at(&svg, bar) == plot_left);
            let ghost_x = svg
                .match_indices("<text")
                .filter_map(|(pos, _)| {
                    let tag = &svg[pos..pos + svg[pos..].find("</text>").unwrap()];
                    (tag.contains("<tspan>Europe") && tag.contains("text-anchor=\"start\"")).then(|| {
                        let x: f64 = tag.split(" x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
                        group_x_at(&svg, pos) + x
                    })
                })
                .fold(f64::MIN, f64::max);
            let (left_gap, right_gap) = (plot_left - left_right, ghost_x - widest_bar_right);
            assert!((right_gap - left_gap).abs() <= 1.01, "left {left_gap} vs right {right_gap}");
        }

        /// A facet's shared category header wraps a long name the way a single chart's axis does.
        #[test]
        fn a_horizontal_facets_row_header_wraps_a_long_category() {
            let long = "Europe Middle East and Africa Enterprise Accounts";
            let mut payload_json = horizontal_facet_payload(json!({"rows": "panel", "columns": null, "scale": "shared"}));
            payload_json["rows"] = json!([
                {"product": long, "panel": "A", "revenue": 60.0},
                {"product": "Short", "panel": "A", "revenue": 200.0},
                {"product": long, "panel": "B", "revenue": 60.0},
                {"product": "Short", "panel": "B", "revenue": 45.0},
            ]);
            let svg = crate::text::expand_lines(rendered(payload_json));
            assert_eq!(svg.matches("<tspan>Europe Middle East and Africa</tspan><tspan x=").count(), 2, "one wrapped header a row: {svg}");
        }

        /// Whether a grouped bar's color is 1:1 with its category is decided over
        /// the whole chart (`_is_color_1to1_with_x`): a panel whose own colors
        /// happen to be 1:1 still splits its bands like its siblings.
        #[test]
        fn a_horizontal_facets_color_split_is_decided_over_every_panel() {
            let mut payload_json = horizontal_facet_payload(json!({"rows": "panel", "columns": null, "scale": "shared"}));
            payload_json["resolved"]["color"] = json!("seg");
            payload_json["rows"] = json!([
                {"product": "North", "panel": "A", "seg": "a", "revenue": 800.0},
                {"product": "North", "panel": "A", "seg": "b", "revenue": 700.0},
                {"product": "South", "panel": "A", "seg": "a", "revenue": 600.0},
                {"product": "South", "panel": "A", "seg": "b", "revenue": 500.0},
                {"product": "North", "panel": "B", "seg": "a", "revenue": 300.0},
                {"product": "South", "panel": "B", "seg": "b", "revenue": 200.0},
            ]);
            let svg = rendered(payload_json);
            let heights: std::collections::BTreeSet<String> = svg
                .split("<rect ")
                .skip(2)
                .filter_map(|rect| rect.split("height=\"").nth(1)?.split('"').next().map(str::to_string))
                .filter(|h| h != "0.00")
                .collect();
            assert_eq!(heights.len(), 1, "every bar is one sub-band tall: {heights:?} {svg}");
        }

        fn layered_facet_payload(mutate_layer: impl FnOnce(&mut serde_json::Value)) -> serde_json::Value {
            let mut payload_json = make_facet_payload("shared");
            for row in payload_json["rows"].as_array_mut().unwrap() {
                row["target"] = json!(1000.0);
            }
            let mut layer = line_layer_json("#e45756");
            mutate_layer(&mut layer);
            payload_json["resolved"]["layers"] = json!([layer]);
            payload_json
        }

        fn rendered(payload_json: serde_json::Value) -> String {
            render(&make_payload(payload_json), &test_fonts()).expect("renders")
        }

        #[test]
        fn a_facet_line_layer_with_its_own_color_renders() {
            let plain = rendered(layered_facet_payload(|_| {}));
            let colored = rendered(layered_facet_payload(|l| {
                l["color"] = json!("region");
                l["line_mark"]["stroke"]["color"] = json!(null);
                l["point_mark"]["color"] = json!(null);
            }));
            assert_ne!(colored, plain, "a layer with its own color grouping draws its own series");
        }

        #[test]
        fn a_facet_line_layer_with_value_labels_renders() {
            let plain = rendered(layered_facet_payload(|_| {}));
            let labeled = rendered(layered_facet_payload(|l| l["line_mark"]["labels"]["visible"] = json!(true)));
            assert!(labeled.matches("<text").count() > plain.matches("<text").count());
        }

        /// A layer reading its own query owns that dataset: every cell draws all of
        /// it, whichever panel the base rows belong to.
        #[test]
        fn a_facet_layer_reading_another_query_draws_all_of_it_in_every_panel() {
            let mut payload_json = layered_facet_payload(|l| l["query_name"] = json!("other"));
            payload_json["datasets"] = json!({"other": [
                {"product": "Jan", "target": 1000.0},
                {"product": "Feb", "target": 1400.0},
                {"product": "Mar", "target": 800.0},
            ]});
            let svg = rendered(payload_json);
            let paths: Vec<&str> = svg
                .match_indices("fill=\"none\" stroke=\"#e45756\"")
                .map(|(i, _)| svg[svg[..i].rfind("<path d=\"").expect("a path") + 9..].split('"').next().expect("d"))
                .collect();
            assert_eq!(paths.len(), 2, "one line per panel: {svg}");
            assert_eq!(paths[0], paths[1], "both cells draw the same whole dataset");
        }

        /// A step layer on another query doubles that query's rows, not the base's.
        #[test]
        fn a_facet_step_layer_reading_another_query_doubles_its_own_dataset() {
            let render_with = |target: f64| {
                let mut payload_json = layered_facet_payload(|l| {
                    l["query_name"] = json!("other");
                    l["line_mark"]["curve"] = json!("step");
                });
                payload_json["datasets"] = json!({"other": [
                    {"product": "Jan", "target": target},
                    {"product": "Feb", "target": target},
                    {"product": "Mar", "target": target},
                ]});
                rendered(payload_json)
            };
            assert_ne!(render_with(500.0), render_with(1500.0));
        }

        #[test]
        fn a_facet_bar_layer_draws_in_every_panel() {
            let mut payload_json = make_facet_payload("shared");
            payload_json["resolved"]["layers"] = json!([super::layers::bar_layer_json(None)]);
            for row in payload_json["rows"].as_array_mut().unwrap() {
                row["target"] = json!(1000.0);
            }
            let svg = rendered(payload_json);
            assert_eq!(svg.matches("layer_1_marks").count(), 2, "one bar layer group per panel: {svg}");
        }

        #[test]
        fn a_facet_area_step_or_disconnected_layer_renders() {
            rendered(layered_facet_payload(|l| {
                l["type"] = json!("area");
                l["area_mark"] = json!({"opacity": 0.2});
            }));
            rendered(layered_facet_payload(|l| {
                l["line_mark"]["curve"] = json!("step");
                l["line_mark"]["connect"] = json!(false);
            }));
            rendered(layered_facet_payload(|l| l["line_mark"]["connect"] = json!(false)));
        }

        /// A band-step layer's doubled rows are its own dataset, so under multiples
        /// every cell holds the whole chart's rows, a disconnected one a path per band.
        #[test]
        fn a_facet_step_layer_draws_every_panels_rows_in_each_cell() {
            let mut payload_json = layered_facet_payload(|l| {
                l["line_mark"]["curve"] = json!("step");
                l["line_mark"]["connect"] = json!(false);
            });
            for (i, row) in payload_json["rows"].as_array_mut().unwrap().iter_mut().enumerate() {
                row["target"] = json!(1000.0 + 100.0 * (i / 3) as f64);
            }
            payload_json["resolved"]["color"] = json!("product");
            let svg = rendered(payload_json);
            let paths: Vec<&str> = svg
                .match_indices("fill=\"none\" stroke=\"#e45756\"")
                .map(|(i, _)| svg[svg[..i].rfind("<path d=\"").expect("a path") + 9..].split('"').next().expect("d"))
                .collect();
            assert_eq!(paths.len(), 2 * 3, "three band paths in each of two cells: {svg}");
            for d in paths {
                let mut ys: Vec<&str> =
                    d.split(['M', 'L']).filter_map(|point| point.split_once(',').map(|(_, y)| y)).collect();
                ys.sort_unstable();
                ys.dedup();
                assert_eq!(ys.len(), 2, "a band's path holds both panels' rows: {d}");
            }
        }

        #[test]
        fn a_facet_dual_axis_line_layer_refuses() {
            let payload_json = layered_facet_payload(|l| l["axis_y"]["position"] = json!("right"));
            let err = render(&make_payload(payload_json), &test_fonts()).unwrap_err();
            assert_eq!(err, "bar: multiples with a dual-axis layer not supported");
        }

        #[test]
        fn a_facet_line_layer_over_a_grouped_base_or_independent_scale_renders() {
            let colored = layered_facet_payload(|_| {});
            let mut grouped = colored.clone();
            grouped["resolved"]["color"] = json!("product");
            let mut independent = colored;
            independent["resolved"]["multiples"]["scale"] = json!("independent");
            rendered(grouped);
            rendered(independent);
        }

        #[test]
        fn a_plain_facet_line_layer_renders() {
            let payload_json = layered_facet_payload(|_| {});
            render(&make_payload(payload_json), &test_fonts()).expect("renders");
        }

        /// A layer on a continuous x draws in every panel, over that panel's own rows.
        #[test]
        fn a_continuous_x_facet_with_a_layer_draws_it_in_each_panel() {
            let xs: Vec<(f64, f64)> = (0..4).map(|h| (h as f64 * 6.0, 100.0 + h as f64)).collect();
            let mut payload_json = make_continuous_payload(&xs);
            payload_json["resolved"]["title"] = json!(null);
            payload_json["resolved"]["multiples"] =
                json!({"rows": "region", "columns": null, "scale": "shared"});
            payload_json["resolved"]["panel_axes"] =
                json!([{"field": "region", "values": ["\"North\"", "\"South\""]}]);
            payload_json["rows"] = json!(xs
                .iter()
                .flat_map(|(x, y)| vec![
                    json!({"product": x, "region": "North", "revenue": y, "target": 50.0}),
                    json!({"product": x, "region": "South", "revenue": y, "target": 50.0}),
                ])
                .collect::<Vec<_>>());
            payload_json["resolved"]["layers"] = json!([line_layer_json("#e45756")]);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert_eq!(svg.matches("stroke=\"#e45756\"").count() >= 2, true, "one layer path per panel");
        }

        /// A panel's value axis draws its bars from the zero rule wherever a
        /// negative floor puts it.
        #[test]
        fn a_negative_floor_panel_draws_from_the_zero_rule() {
            let mut payload_json = make_facet_payload("shared");
            payload_json["rows"][0]["revenue"] = json!(-300.0);
            payload_json["resolved"]["style"]["axis_y"]["tick_values"] =
                json!([-500.0, 0.0, 500.0, 1000.0, 1500.0, 2000.0]);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert!(svg.contains('\u{2212}'), "the negative rung is labeled: {svg}");
        }

        /// `render_facet` reads the chart's own background once (`bg`, see
        /// `BarResolved::background`) and hands that one value to the
        /// composite canvas rect and to both panel calls, whose inside-bar
        /// label fill it also defaults; pinning the composite rect pins
        /// the single read.
        #[test]
        fn facet_composite_rect_reads_the_chart_own_background_not_the_board_background() {
            let mut payload_json = make_facet_payload("shared");
            payload_json["resolved"]["background"] = json!("#d9a45b");
            payload_json["background"] = json!("#ffffff");
            let payload = make_payload(payload_json);
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("renders");
            assert!(
                svg.contains("<rect width=\"557\" height=\"433\" fill=\"#d9a45b\"/>"),
                "composite canvas rect paints the chart's own background: {svg}"
            );
        }

        /// Front-end contract: every panel is a DIRECT child of ONE
        /// `role-scope cell` group (Vega's facet cell), and nothing else is --
        /// `chart_interactivity.js`'s `panelScope` finds a mark's panel by
        /// walking up to the child of that group, `panelAtPoint` iterates the
        /// group's children as panels, and `panelXAxis` matches a panel's
        /// ordinal among them to a column's axis. A row header among the
        /// children would be read as a panel.
        #[test]
        fn facet_panels_are_the_direct_children_of_one_role_scope_cell() {
            let open = r#"<g class="mark-group role-scope cell" role="graphics-object" aria-roledescription="group mark container">"#;
            for (label, multiples) in [
                ("rows", json!({"rows": "region", "columns": null, "scale": "shared"})),
                ("columns", json!({"rows": null, "columns": "region", "scale": "shared"})),
            ] {
                let mut payload_json = make_facet_payload("shared");
                payload_json["resolved"]["multiples"] = multiples;
                let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
                assert_eq!(svg.matches(open).count(), 1, "{label}: one cell: {svg}");
                let inner_start = svg.find(open).unwrap() + open.len();
                let (mut depth, mut children, mut stray) = (1, 0, 0);
                let mut inner_end = None;
                for (at, _) in svg[inner_start..].match_indices('<') {
                    let tag = &svg[inner_start + at..];
                    if tag.starts_with("</g>") {
                        depth -= 1;
                        if depth == 0 {
                            inner_end = Some(inner_start + at);
                            break;
                        }
                    } else if tag.starts_with("<g ") || tag.starts_with("<g>") {
                        children += usize::from(depth == 1);
                        depth += 1;
                    } else if depth == 1 && !tag.starts_with("</") {
                        stray += 1;
                    }
                }
                let inner = &svg[inner_start..inner_end.expect("the cell closes")];
                assert_eq!(children, 2, "{label}: North and South are the cell's children: {inner}");
                assert_eq!(stray, 0, "{label}: no header or axis run among the panels: {inner}");
            }
        }

        /// Coordinate-asserting regression for three of this composer's own
        /// geometry laws at once, each independently mutation-checked
        /// (`cargo test -p chart-renderer` run against the mutated source,
        /// confirmed FAILED on this exact assertion, then reverted):
        ///
        /// - Deleting the footer descent term (`bar.rs`'s `footer_reach =
        ///   max_y_attr(...) + (x_label_fs - (0.8 * x_label_fs).round())`,
        ///   the whole content of commit `464f84f8aa`) shrinks the reserved
        ///   footer band by 2px, failing the second assertion below.
        /// - Forcing either facet arm's `suppress_x_axis` pad-autosize
        ///   flag back to the legacy `fit_plot` shrink path (reverting
        ///   `ef9e5d5e12`'s whole port) computes a materially different
        ///   `panel_w`/`child_height` from an unrelated algorithm — the
        ///   canvas size assertion fails.
        /// - Hardcoding the composite's own top-margin reserve to `0.0`
        ///   (both `header_band.max(...)` reads in `render_facet`) moves
        ///   North's own panel from `translate(38.00,6.00)` and South's
        ///   from `translate(38.00,220.00)` up to `translate(38.00,0.00)`/
        ///   `translate(38.00,217.00)` — confirmed by running that exact
        ///   mutation against this exact test. The composite's own
        ///   height-correction pass folds this same top-margin term into
        ///   ITS OWN naive/probed measurements too (both `margin_top`
        ///   reads happen before their own `canvas_for` call), so the 6px
        ///   reserve for THIS fixture's own baked tick ladder is mostly
        ///   absorbed back into a slightly smaller `child_height` (195 →
        ///   192, real code vs. this mutation) rather than growing the
        ///   canvas — the canvas size assertion below stays unchanged
        ///   (557×433) even though the reserve is real and load-bearing
        ///   on the translate.
        ///
        /// `value_domain_rows`'s own law (below this test) needs a THIRD,
        /// separate fixture: this payload's `axis_y` bakes `tick_values`/
        /// `domain_max` (2000.0, fixed regardless of row data — production's
        /// own resolve-time behavior for a BAKED axis), so mutating
        /// `value_domain_rows` to always-`None` here is provably a no-op —
        /// verified: that exact mutation left this test green, byte-
        /// identical output. `shared_scale_gives_every_panel_the_same_
        /// domain_not_its_own` below unbakes `axis_y` for that reason.
        #[test]
        fn rows_only_facet_geometry_matches_pinned_coordinates() {
            let payload = make_payload(make_facet_payload("shared"));
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("renders");
            assert!(
                svg.starts_with(
                    r#"<svg xmlns="http://www.w3.org/2000/svg" width="557" height="433" viewBox="0 0 557 433">"#
                ),
                "canvas size (pad-autosize arms; `canvas_for`'s own content origin is `canvas_x(0)`, \
                 `left_mark_overhang` short of `margin_left`, never `margin_left` alone, so it is \
                 reserved once — the row_footer's own anchor then reaches `left_mark_overhang` past \
                 `plot_w`, and the ink fold that reserves canvas width reaches the same distance): {svg}"
            );
            assert!(
                svg.contains(r#"<g transform="translate(38.00,6.00)">"#),
                "North panel's own row offset (the composite's own top-margin reserve, canvas-edge frame — \
                 `canvas_x` subtracts `left_mark_overhang` (2.0) from the raw grid offset): {svg}"
            );
            assert!(
                svg.contains(r#"<g transform="translate(38.00,221.00)">"#),
                "South panel's own row offset (top-margin reserve + footer descent + per-panel ink.y2): {svg}"
            );
            // The rows-only value axis (real Vega's `row_footer`) anchors
            // at `ceil(cell x2)` (`vega-view-transforms/src/layout/
            // grid.js:262`, `ceil` from `grid.js:13`'s own `agg` callback)
            // — the bar's own crisp-stroke overhang past `plot_w`
            // (`left_mark_overhang`, `2.0` on this fixture), not `plot_w`
            // alone. North's own "1,000" tick label (local frame, inside
            // its own row's `<g>`) sits `left_mark_overhang` right of the
            // un-shifted `517.20` this rung painted before this fix.
            assert!(
                svg.contains(r#"<text x="519.20" y="99.75" text-anchor="end""#)
                    && svg.contains(">1,000<"),
                "North's own value-axis label reaches `left_mark_overhang` past `plot_w`: {svg}"
            );
        }

        /// `value_domain_rows`'s own law, isolated from the baked-axis test
        /// above: an UNBAKED `axis_y` (empty `tick_values`, null
        /// `domain_max`) computes its ladder from row data at render time,
        /// so `multiples.scale: "shared"` feeding every panel the WHOLE
        /// chart's rows (not each panel's own subset) is the only thing
        /// that can put South's own gridlines at the SAME pixel positions
        /// as North's shared-domain ladder. Mutation-checked: forcing
        /// `value_domain_rows = None` (`bar.rs:3871`, always each panel's
        /// own smaller subset — South's own max is 2100, not North's 2400)
        /// confirmed FAILED here (South's own gridline moved to `y1="151.50"`,
        /// its ladder now niced off its OWN smaller domain) before being
        /// reverted — the top TICK LABEL text alone is not sensitive to this
        /// mutation (both domains still nice to the same "2,000" label), so
        /// this pins a gridline coordinate instead.
        #[test]
        fn shared_scale_gives_every_panel_the_same_domain_not_its_own() {
            let mut payload_json = make_facet_payload("shared");
            let axis_y = &mut payload_json["resolved"]["style"]["axis_y"];
            axis_y["tick_values"] = json!([]);
            axis_y["domain_max"] = json!(null);
            let payload = make_payload(payload_json);
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("renders");
            assert_eq!(
                svg.matches(r#"<line x1="0.5" y1="155.50" x2="480.50" y2="155.50""#).count(),
                2,
                "both panels' gridline ladder shares the SAME pixel positions (North's shared domain, not South's own smaller one): {svg}"
            );
        }

        #[test]
        fn rows_only_facet_draws_one_panel_per_row_header() {
            let payload = make_payload(make_facet_payload("shared"));
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("renders");

            assert!(svg.contains(">North<"), "row header North present: {svg}");
            assert!(svg.contains(">South<"), "row header South present: {svg}");
            // 3 months x 2 panels = 6 bars total (rounded corners: a `<path
            // d="M...Z" fill="#3164a3">`, not a `<rect>`, per this test's
            // default `mark.border.radius`).
            assert_eq!(svg.matches("fill=\"#3164a3\"").count(), 6, "one bar fill per bar: {svg}");
            // The category axis (Jan/Feb/Mar) draws once, not once per row —
            // `suppress_x_axis` on every panel but the last.
            assert_eq!(svg.matches(">Jan<").count(), 1, "shared x-axis draws once: {svg}");
        }

        /// A rows-only, independent-scale payload mirroring `fixtures/
        /// multiples/05-independent-scale`'s own real data: East (1,200 —
        /// 2,400) and West (90,000 — 210,000), two orders of magnitude
        /// apart — the shape that exercises the whole-dataset gutter
        /// estimate and each panel's own unbaked domain. `axis_y` is
        /// UNBAKED (empty `tick_values`, null `domain_max`) — Python's
        /// `fold_panels` (`_chart_rows.py`) returns `None` for `multiples.
        /// scale: "independent"`, so a real independent-scale chart never
        /// reaches `render_facet` with a baked ladder.
        fn make_independent_facet_payload() -> serde_json::Value {
            let mut payload_json =
                make_vertical_payload(&[("Jan", 1200.0), ("Feb", 1800.0), ("Mar", 2400.0)], false);
            payload_json["resolved"]["title"] = json!(null);
            payload_json["resolved"]["style"]["axis_y"]["position"] = json!("right");
            payload_json["resolved"]["style"]["axis_y"]["ticks"]["visible"] = json!(false);
            let axis_y = &mut payload_json["resolved"]["style"]["axis_y"];
            axis_y["tick_values"] = json!([]);
            axis_y["domain_max"] = json!(null);
            payload_json["resolved"]["multiples"] =
                json!({"rows": "region", "columns": null, "scale": "independent"});
            payload_json["resolved"]["panel_axes"] =
                json!([{"field": "region", "values": ["\"East\"", "\"West\""]}]);
            payload_json["rows"] = json!([
                {"product": "Jan", "region": "East", "revenue": 1200.0},
                {"product": "Feb", "region": "East", "revenue": 1800.0},
                {"product": "Mar", "region": "East", "revenue": 2400.0},
                {"product": "Jan", "region": "West", "revenue": 90000.0},
                {"product": "Feb", "region": "West", "revenue": 150000.0},
                {"product": "Mar", "region": "West", "revenue": 210000.0},
            ]);
            payload_json["width"] = json!(600.0);
            payload_json["height"] = json!(432.0);
            payload_json["padding"] = json!({"left": 16.0, "right": 16.0, "top": 16.0, "bottom": 16.0});
            payload_json
        }

        /// Pins the two laws this port ships for `multiples.scale:
        /// "independent"` on a rows-only facet, taken from `fixtures/
        /// multiples/05-independent-scale`'s own real reference SVG
        /// (`the retired per-chart sweep --group fixtures/multiples`, canvas 578×432,
        /// MISMATCH 4.5% — the row_footer/embedded-axis and gutter-estimate
        /// laws land canvas width and every value-axis label's own `x`
        /// exact; the still-open residual is a vertical one, the single-
        /// pass facet-overshoot correction's own approximation on a domain
        /// with no headroom — see `docs/laws-shared.md`'s facet ledger).
        /// This fixture's own `axis_y` carries no `tick_label.si_format`
        /// (`make_vertical_payload`'s own base style), so its ladder prints
        /// comma-grouped, not `05-independent-scale`'s own SI-compact
        /// strings — the domain-independence law is identical either way.
        ///
        /// - Each panel gets its OWN unbaked ladder from its OWN rows —
        ///   East (max 2,400) draws `0/500/1,000/1,500/2,000`; West (max
        ///   210,000) draws `0/50,000/100,000/150,000/200,000` — CONFIRMED
        ///   distinct, not North/South's shared-scale union.
        /// - Both panels' value-axis label shares the SAME `x` anchor
        ///   despite the wildly different ladders — production's own
        ///   `measure_axis_to_vl` (`vl_field_maps.py:298-401`) computes the
        ///   gutter ONCE, from `numeric_values` over the WHOLE chart's rows
        ///   (`estimated_quantitative_tick_labels`'s cover-ladder, `_measured_
        ///   label_padding.py`), never per panel — `bar.rs`'s own
        ///   `estimated_val_gutter` ports this (reached whenever `ax_y.
        ///   tick_values.is_empty()` — the independent-scale facet's own
        ///   unbaked case here, but also a single-chart unbaked right-axis
        ///   bar when `ax_y.ticks.count` is `None`, matching production).
        ///
        /// Mutation-checked: reverting `value_domain_rows` to the always-
        /// `Some(whole chart's rows)` override (the shared-scale law) makes
        /// both panels draw the SAME ladder (East's own, since its max is
        /// larger) — confirmed FAILED on the West-ladder assertion below,
        /// then reverted. Reverting `estimated_val_gutter` to `gutter_of`
        /// (drawn-ticks-only) makes the two panels' anchors diverge (East's
        /// narrower `1,500`/`2,000` labels vs West's wider `150,000`/
        /// `200,000`) — confirmed FAILED on the "same x for both panels"
        /// assertion, then reverted.
        #[test]
        fn independent_scale_rows_only_gives_each_panel_its_own_domain_and_a_shared_gutter() {
            let payload = make_payload(make_independent_facet_payload());
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("rows-only independent scale renders");
            // The embedded axis's own law: no `row_footer`-style right
            // reserve (`value_axis_right_overhang`/`plot_left`) folded into
            // the panel's own ink — mutation-checked: forcing `independent_
            // scale: false` at this composer's own call site widens
            // the canvas to `599` (the `row_footer`-reserve shape), FAILED
            // here, then reverted.
            assert!(
                svg.starts_with(
                    r#"<svg xmlns="http://www.w3.org/2000/svg" width="595" height="433" viewBox="0 0 595 433">"#
                ),
                "canvas width (no row_footer-style right reserve folded in): {svg}"
            );
            assert!(
                svg.contains(">500<") && svg.contains(">2,000<"),
                "East's own ladder: {svg}"
            );
            assert!(
                svg.contains(">50,000<") && svg.contains(">200,000<"),
                "West's own ladder, distinct from East's: {svg}"
            );
            // Every value-axis label (both panels' full ladders) shares the
            // SAME `x=` — the whole-dataset gutter estimate, not a
            // per-panel measurement of the drawn ladder. Value-axis labels
            // are the only `<text x="..." ... text-anchor="end">` elements
            // this fixture emits (category labels anchor `"middle"`, the
            // row-header titles anchor `"start"`).
            let mut xs = std::collections::HashSet::new();
            for (i, _) in svg.match_indices("<text x=\"") {
                let tag_end = svg[i..].find('>').map(|e| i + e).unwrap_or(svg.len());
                if svg[i..tag_end].contains("text-anchor=\"end\"") {
                    let rest = &svg[i + "<text x=\"".len()..];
                    xs.insert(&rest[..rest.find('"').expect("x attribute closes")]);
                }
            }
            assert_eq!(xs.len(), 1, "every value-axis label shares one x anchor: {svg}");
        }

        /// Real Vega repeats the value axis once per CELL under an independent
        /// resolve, so a columns facet paints every column's own ladder, not just
        /// the last column's.
        #[test]
        fn independent_scale_columns_paint_each_columns_own_axis() {
            let mut payload_json = make_independent_facet_payload();
            payload_json["resolved"]["panel_axes"] =
                json!([{"field": "region", "values": ["\"East\"", "\"West\""]}]);
            payload_json["resolved"]["multiples"] =
                json!({"rows": null, "columns": "region", "scale": "independent"});
            let payload = make_payload(payload_json);
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("columns independent scale renders");
            assert!(svg.contains(">2,400<") && svg.contains(">1,000<"), "East's own ladder");
            assert!(svg.contains(">200,000<") && svg.contains(">20,000<"), "West's own ladder");
        }

        /// Production refuses a mirror under an independent scale at resolve time
        /// (`ERR_MULTIPLES_INDEPENDENT_SCALE_MIRROR`, `_axes.py`'s
        /// `_apply_multiples_mirror`); the renderer reports the same error.
        #[test]
        fn independent_scale_with_mirror_is_the_production_error() {
            let mut payload_json = make_independent_facet_payload();
            payload_json["resolved"]["style"]["axis_y"]["mirror"] = json!(true);
            let payload = make_payload(payload_json);
            let fonts = test_fonts();
            let err = render(&payload, &fonts).unwrap_err();
            assert!(err.contains("ERR-MULTIPLES-INDEPENDENT-SCALE-MIRROR"), "{err}");
        }

        /// Production's `BaselineFeature.apply` returns early under an
        /// independent multiples scale (`render/chart/features/
        /// baseline.py:310-321`, `multiples_scale_independent(chart)`) —
        /// no zero/threshold rule layer exists on an independent-scale
        /// panel at all, because the verdict would be computed off the
        /// pooled union of every panel's own rows, wrong for a panel with
        /// its own domain. This fixture's own `axis_y.grid.threshold` is
        /// set (`#8A8C8F` width 2), and that rule's own ink also doubles
        /// as the `left_mark_overhang` shifting bars, grid and the x-axis
        /// category labels 2px right of the reference (x 52..532 instead
        /// of 50..530) when it wrongly paints — mutation-checked:
        /// reverting `zero_w` to
        /// `zero_w_for(ax_y)` unconditionally (dropping the `independent_
        /// scale` gate) makes this fail on both assertions below.
        #[test]
        fn independent_scale_draws_no_zero_rule_and_bars_stay_at_the_true_left_edge() {
            let payload = make_payload(make_independent_facet_payload());
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("rows-only independent scale renders");
            assert!(!svg.contains("#8A8C8F"), "no zero/threshold rule ink under independent scale: {svg}");
            assert!(!svg.contains("stroke-width=\"2\""), "no 2px zero-rule stroke: {svg}");
            assert!(
                svg.contains("translate(50.00,16.00)") && svg.contains("translate(50.00,218.00)"),
                "each panel's plot origin sits at x=50, not 52 (no zero-rule left_mark_overhang): {svg}"
            );
        }

        /// The embedded axis's own bottom-most "0" label ink must exclude
        /// the axis group's own 0.5 crisp `translate` — Vega's own
        /// `axisLayout` (`vega-view-transforms/src/layout/axis.js:77`)
        /// shifts the axis's bounds by `(x, y)` only; the crisp delta
        /// (`axis.js:22`) lands on the painted `item.x`/`item.y`
        /// (`:79`), never in the bounds `gridLayout` reads. Including it
        /// anyway measures the descent 0.5px too generous, which crosses
        /// a `ceil` `correct_facet_child_extent`'s own overshoot
        /// arithmetic wouldn't otherwise cross at this payload height —
        /// `independent_scale_bar_stroke_and_bottom_label_ink_shrink_
        /// the_panel`'s own `height: 432` does not cross it, so it alone
        /// cannot pin this law. Mutation-checked: folding `val_y_delta`
        /// back into the ink (`tick_y` instead of `tick_y - val_y_delta`)
        /// moves the zero gridline from `y=59.50` to `y=60.50` (checked at this height
        /// against heights 190..=215), FAILED,
        /// then reverted.
        #[test]
        fn independent_scale_bottom_label_ink_excludes_the_axis_crisp_delta() {
            let mut j = make_independent_facet_payload();
            j["resolved"]["style"]["mark"]["border"] =
                json!({"width": 1.0, "color": "#FFFFFF", "radius": 2.0});
            j["height"] = json!(201.0);
            let payload = make_payload(j);
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("renders");
            assert!(
                svg.contains("<line x1=\"0.5\" y1=\"59.50\""),
                "the bottom \"0\" label's own descent excludes the axis's crisp 0.5 translate: {svg}"
            );
        }

        /// Two ink terms an independent-scale panel's EMBEDDED value axis
        /// carries that a shared-scale panel's separate `row_footer` role
        /// never does.
        ///
        /// The first is the bar's own mark border stroke: this fixture's
        /// own `mark.border` (width 1, matching the real fixture's white
        /// separator stroke) makes the topmost panel's own Mar bar touch
        /// `y=0` exactly (the unbaked, unauthored vertical domain never
        /// nices — `emitters/bar.py`'s own `bar_zero` branch), so its
        /// border stroke overhangs above `y=0`. Real Vega's own
        /// `viewSizeLayout` (`ViewLayout.js:165`) counts that ink exactly
        /// ONCE, as `top = ceil(-viewBounds.y1)` — `PanelGeometry.ink.y1`
        /// (from the mark's own bounds, `bar_ink`) already carries this
        /// bar's stroke, so `compose_facet`'s own `margin_top`/`probe_
        /// margin_top` fold it in from THAT field, never a second,
        /// separately-derived term. Mutation-checked: dropping the
        /// `(-ink.y1).max(0.0)` term from both `margin_top` formulas
        /// drops the facet root from `y=17` back to `y=16`, FAILED, then
        /// reverted.
        ///
        /// The second is the embedded axis's own bottom-most "0" label:
        /// its descent past `plot_bottom` is real row-content height too
        /// — shrinking each row's own drawn content (the zero gridline
        /// sits at `y=175.5` here, one pixel inside this port's own
        /// gridline-vs-cell-height convention, `01-rows-only`'s cell 179
        /// ↔ gridline 179.5) while the row-to-row OFFSET (`translate(50,
        /// 218)`, the SAME 201px gap `01-rows-only` uses) is unaffected —
        /// this term only ever changes what fits INSIDE a row, never the
        /// row's own outer position. Mutation-checked: gating this term
        /// off drops the zero gridline to `y=179.5`, FAILED, then
        /// reverted.
        ///
        /// `bar_ink` also stays canvas-exact: a mark's own bounds are the
        /// RECT's own box, stroke-expanded — never the whole plot rect's
        /// `[plot_left, plot_right]` span, which reaches past every bar's
        /// own band padding. Mutation-checked: expanding `bar_ink` in x
        /// too grows this canvas to `596`, FAILED, then reverted.
        #[test]
        fn independent_scale_bar_stroke_and_bottom_label_ink_shrink_the_panel() {
            let mut j = make_independent_facet_payload();
            j["resolved"]["style"]["mark"]["border"] =
                json!({"width": 1.0, "color": "#FFFFFF", "radius": 2.0});
            let payload = make_payload(j);
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("renders");
            assert!(
                svg.starts_with(r#"<svg xmlns="http://www.w3.org/2000/svg" width="595""#),
                "this fixture's own default band padding already covers the mark's stroke in x, \
                 so the real per-bar rect union is a no-op here: {svg}"
            );
            assert!(
                svg.contains("translate(50.00,17.00)") && svg.contains("translate(50.00,219.00)"),
                "the topmost bar's own border stroke overhang reaches the facet's own outer top margin ONCE, and the row-to-row offset stays fixed: {svg}"
            );
            assert!(
                svg.contains("y1=\"176.50\""),
                "the bottom \"0\" label's own descent shrinks the panel's own drawn content: {svg}"
            );
            assert!(
                svg.contains("x=\"130.00\" y=\"415.50\""),
                "the shared x-axis footer sits below the row-to-row offset above, unaffected by either ink term: {svg}"
            );
        }

        /// `mark.band_width: 1` zeroes out `col_pad` (`bar.rs:5171-5175`'s
        /// own single-series `band_pi`/`band_po: 0.0` law leaves no OTHER
        /// outer padding either), so the outer bars' own rects sit flush
        /// against the plot edge — exactly the shape whose real Vega
        /// bound (`vega.js:15959-15965`, `boundStroke`) pushes the mark's
        /// own 1px border stroke past `[plot_left, plot_right]`, widening
        /// the panel by the uncovered `1px` on each side. Mutation-
        /// checked: reverting `bar_ink`'s x-extent to the unexpanded
        /// `[plot_left, plot_right]` span drops this canvas back to the
        /// un-widened `595`, FAILED, then reverted.
        #[test]
        fn independent_scale_full_width_band_stroke_widens_the_panel_in_x() {
            let mut j = make_independent_facet_payload();
            j["resolved"]["style"]["mark"]["border"] =
                json!({"width": 1.0, "color": "#FFFFFF", "radius": 2.0});
            j["resolved"]["style"]["mark"]["band_width"] = json!(1.0);
            let payload = make_payload(j);
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("renders");
            assert!(
                svg.starts_with(r#"<svg xmlns="http://www.w3.org/2000/svg" width="596""#),
                "a full-width band's own stroke reaches past the plot edge, widening the panel by \
                 the uncovered 1px on each side: {svg}"
            );
        }

        /// An axis title wider than a panel wraps against the panel's extent.
        #[test]
        fn a_facet_axis_title_wider_than_its_panel_wraps() {
            let mut payload_json = make_facet_payload("shared");
            payload_json["resolved"]["x_label"] = json!(
                "An axis title far too long to fit the width of a single small multiple panel, \
                 however the wrapper would break it across lines"
            );
            payload_json["resolved"]["style"]["axis_x"]["title"]["visible"] = json!(true);
            payload_json["resolved"]["style"]["axis_x"]["title"]["font"]["weight"] = json!("600");
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert!(svg.contains("<tspan"), "the title wraps onto a second line: {svg}");
        }

        /// `_correct_facet_overshoot` skips a shrink that would leave a panel
        /// non-positive (`converters/chart.py:809-813`): the naive height
        /// stays and the composite renders past a card too small for it.
        #[test]
        fn facet_height_collapse_keeps_the_naive_height() {
            let mut payload_json = make_facet_payload("shared");
            payload_json["height"] = json!(5.0);
            payload_json["resolved"]["x_label"] = json!("");
            payload_json["resolved"]["y_label"] = json!("");
            let mut payload = make_payload(payload_json);
            // A sizing pass: the composite is not solved onto a box it cannot fit.
            payload.measuring = true;
            let fonts = test_fonts();
            render(&payload, &fonts).expect("renders at the naive panel height");
        }

        #[test]
        fn multiples_columns_only_draws_two_panels_side_by_side() {
            let mut payload_json = make_facet_payload("shared");
            payload_json["resolved"]["multiples"] =
                json!({"rows": null, "columns": "region", "scale": "shared"});
            let payload = make_payload(payload_json);
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("columns-only facet renders");
            assert!(svg.contains(">North<"), "column header North present: {svg}");
            assert!(svg.contains(">South<"), "column header South present: {svg}");
            assert!(
                svg.contains(r#"<g transform="translate(0.00,20.00)">"#),
                "North panel sits at the composite's own left origin, below the column-header \
                 band — the topmost row's own value-axis overhang (6px on this shape) UNIONS \
                 against that band (ViewLayout.js:72-82), never sums with it, so the smaller \
                 axis term never moves this translate past the band alone: {svg}"
            );
            assert!(
                svg.contains(r#"<g transform="translate(264.00,20.00)">"#),
                "South panel's own column offset (North's own panel width + grid padding): {svg}"
            );
        }

        /// The union law, both branches: the composite's own top
        /// margin is `ceil(max(column-header band, axis overhang))`
        /// (`ViewLayout.js:72-82`'s union into one `viewBounds`), never
        /// their sum. The test above already covers the branch where the
        /// header band (20px) wins over a smaller overhang (6px on that
        /// shape) — this test covers the OTHER branch, where a big enough
        /// axis-label font makes the overhang win instead. A `44px` value
        /// axis label font gives `dy = round(0.30·44) − round(0.8·44) =
        /// 13 − 35 = −22` (`vega_fit::text_ink`'s own baseline-shift
        /// formula, `baseline_shift("middle", 44) = 13`), so the topmost
        /// tick's own unclamped ink reaches `22px` above local `y=0` —
        /// past the `20px` header band — and `margin_top` must follow the
        /// axis term instead: `ceil(max(20, 22)) = 22`.
        #[test]
        fn columns_only_facet_top_margin_follows_the_larger_of_header_band_or_axis_overhang() {
            let mut payload_json = make_facet_payload("shared");
            payload_json["resolved"]["multiples"] =
                json!({"rows": null, "columns": "region", "scale": "shared"});
            payload_json["resolved"]["style"]["axis_y"]["labels"]["font"]["size"] = json!(44.0);
            let payload = make_payload(payload_json);
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("columns-only facet renders");
            assert!(
                svg.contains(r#"<g transform="translate(0.00,22.00)">"#),
                "the 22px axis overhang now exceeds the 20px header band, so margin_top \
                 follows IT, not their sum (26) or the band alone (20): {svg}"
            );
            // The column header rides the view origin down with the panels
            // in this branch (Vega's own `layoutGroup` places the header
            // group's box at the SAME unioned `viewBounds` origin every
            // other role shares): header_top = pad_t(0) + margin_top(22) -
            // COLUMN_HEADER_BAND_HEIGHT(20) = 2, text_y = header_top +
            // baseline_shift("top", 10) = 2 + 8 = 10 — 2px below the
            // band-wins case's flat `y="8.00"`.
            assert!(
                svg.contains(r#"y="10.00" text-anchor="middle""#),
                "the header anchor must move with margin_top when the axis overhang wins, \
                 not stay pinned at pad_t: {svg}"
            );
        }

        /// Coordinate-asserting regression pinning three facet gates
        /// `raw_right_reach` must read the fit arm's own `m.raw_left`
        /// (`vega_fit::FitMargins.raw_left`'s own doc comment), never a
        /// flat constant: `zero_w_for` only returns a nonzero width when
        /// BOTH `grid.visible` and the threshold are visible, so with
        /// `grid.visible: false` no zero rule fires and the plot's own ink
        /// reaches no further than `[0, panel_w]` — `raw_left` is `0.0`,
        /// giving `row_footer_overhang = ceil(panel_w) - panel_w`, an
        /// integral `panel_w`'s own `0.0`. A flat `2.0` (the OLD constant,
        /// really the zero rule's own default width, hardcoded regardless
        /// of whether the rule actually paints) instead reserves 2px no
        /// mark or rule ever draws into. Mutation: hardcode `raw_right_
        /// reach: 2.0` at this arm's own `PlotRect` construction site
        /// (`render_vertical_panel`) and the "0" value label moves from
        /// `x="515.20"` (canvas 553) to `x="517.20"` (canvas 555).
        #[test]
        fn bar_facet_row_footer_reads_the_real_zero_rule_reach_not_a_flat_constant() {
            let mut payload_json = make_facet_payload("shared");
            payload_json["resolved"]["style"]["axis_y"]["grid"]["visible"] = json!(false);
            let payload = make_payload(payload_json);
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("renders");
            assert!(
                svg.starts_with(
                    r#"<svg xmlns="http://www.w3.org/2000/svg" width="553" height="433" viewBox="0 0 553 433">"#
                ),
                "canvas width reserves the real (zero) zero-rule reach, not the flat 2.0 constant: {svg}"
            );
            assert!(
                svg.contains(r#"x="515.20" y="198.00" text-anchor="end""#) && svg.contains(">0<"),
                "value-axis label anchors at `ceil(panel_w + raw_left) - panel_w` off the real \
                 (zero) reach, not the flat 2.0 constant: {svg}"
            );
        }

        /// Coordinate-asserting regression pinning three facet gates
        /// together (`fold_axis_gutter`, `paint_value_axis`,
        /// `composite_axis_reserve`): a mirrored columns-only facet's own
        /// canvas WIDTH and the shared value axis's single, last-column
        /// label `x`. Mutation-checked (each applied to a scratch copy, `cargo test
        /// -p chart-renderer` re-run, confirmed FAILED, then reverted):
        /// - `fold_axis_gutter` forced `true` (should be `panel_cols_n ==
        ///   1`, false here): folds the axis gutter into EVERY column's own
        ///   ink instead of reserving it once at the composer, inflating
        ///   the canvas width by the gutter's own reach — fails the width
        ///   assertion.
        /// - `paint_value_axis` forced `true` (should gate on the LAST
        ///   column only): draws a second ladder in North's own panel too
        ///   — fails the "exactly one" occurrence count.
        /// - `composite_axis_reserve` forced `0.0` (should be
        ///   `primary_reserve` when `panel_cols_n > 1`): the composite
        ///   never reserves room for the shared axis past the last
        ///   column's own ink — fails the width assertion.
        #[test]
        fn columns_only_facet_with_mirror_pins_canvas_width_and_the_single_value_label() {
            let mut payload_json = make_facet_payload("shared");
            payload_json["resolved"]["multiples"] =
                json!({"rows": null, "columns": "region", "scale": "shared"});
            payload_json["resolved"]["style"]["axis_y"]["mirror"] = json!(true);
            payload_json["resolved"]["style"]["axis_y"]["ticks"]["visible"] = json!(true);
            let payload = make_payload(payload_json);
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("mirrored columns-only facet renders");
            assert!(
                svg.starts_with(
                    r#"<svg xmlns="http://www.w3.org/2000/svg" width="541" height="432" viewBox="0 0 541 432">"#
                ),
                "canvas size (composite_axis_reserve/fold_axis_gutter both correct; `canvas_for`'s own \
                 content origin is `canvas_x(0)`, `left_mark_overhang` short of `margin_left`, so the \
                 double-reserved 2px is gone): {svg}"
            );
            // The primary axis paints its ladder ONCE, in South's own
            // (last-column) panel only — `paint_value_axis` gates every
            // other column's own copy off. The primary and the ghost paint
            // with the IDENTICAL resolved style (same `font-weight`/`fill`
            // — `ghost_labels_paint_with_the_primarys_own_resolved_style`
            // pins that law), so the two copies are told apart by GEOMETRY
            // instead: the primary's `x` sits past South's own plot-right
            // edge plus its tick length and gutter plus the `row_footer` overhang
            // (`245.20`, in South's own panel-LOCAL frame, y=200.00 — this
            // shape's own top-margin reserve UNIONS against the
            // column-header band (20px, ViewLayout.js:72-82) rather than
            // summing with it, and the axis overhang (6px) on this shape
            // never exceeds that band, so `margin_top` stays at the band
            // alone and `child_height` is unaffected by the reserve), the
            // ghost's `x` sits at `primary_reserve` back from the
            // composite's own LEFT edge, in ABSOLUTE coordinates (`31.80`:
            // the ceil'd left margin, `viewSizeLayout`, moves it off the
            // unceiled `30.70`, and the axis group's crisp `0.5` shift joins it; y=220.00 = South's own dy=20 + the same local
            // `200.00` — asserted below).
            assert_eq!(
                svg.matches(r#"x="245.20" y="200.00" text-anchor="end""#).count(),
                1,
                "shared value axis draws its primary ladder exactly once, at the last \
                 column's own right edge, `row_footer_overhang` past the plot: {svg}"
            );
            // The ghost (`axis_y.mirror`'s own opposite-edge copy, painted
            // once per ROW ahead of both panels) anchors at `primary_
            // reserve` from each column's own left edge, never `ghost_
            // reserve` (the whole band): a ghost anchored at `-
            // ghost_reserve` paints its text's RIGHT edge where its LEFT
            // edge belongs, half-clipped off canvas.
            assert_eq!(
                svg.matches(r#"x="31.80" y="220.00" text-anchor="end""#).count(),
                1,
                "ghost label anchors at primary_reserve, not the whole ghost band: {svg}"
            );
            assert!(
                svg.contains(
                    r##"x="31.80" y="220.00" text-anchor="end" font-family="'Inter Variable', Inter, sans-serif" font-size="11" font-weight="400" fill="#626366">1,000<"##
                ),
                "the ghost carries the SAME font-weight/fill as the primary, not a hardcoded \
                 literal: {svg}"
            );
        }

        /// `tick_size` (`primary_reserve = tick_size + label_padding`) must
        /// read the real `axis_y.ticks.length` off the style, never a
        /// hardcoded `6.0` — the mirror fixture above never sets
        /// `ticks.length` explicitly, so it can't tell a real read from a
        /// lucky match against the `unwrap_or(6.0)` default. Renders the
        /// same mirrored columns-only facet at the implicit default tick
        /// length and again with an explicit, far-from-default `ticks.
        /// length: 20.0`, and asserts the canvas WIDTH grows by exactly the
        /// 14px delta on EACH edge: the ghost's left band
        /// (`ghost_reserve = primary_reserve plus the widest label`) and the
        /// primary's own right axis (`composite_axis_reserve`, the measured
        /// ink of an axis whose labels anchor at `tick_size + val_gutter`
        /// past the plot, `utils.py:853-860`). Mutation-checked: hardcoding
        /// `tick_size` to `6.0` regardless of style collapses the delta
        /// to `0.0`.
        #[test]
        fn mirror_reserve_reads_tick_length_from_style_not_a_hardcoded_default() {
            let canvas_width = |tick_length: Option<f64>| {
                let mut payload_json = make_facet_payload("shared");
                payload_json["resolved"]["multiples"] =
                    json!({"rows": null, "columns": "region", "scale": "shared"});
                payload_json["resolved"]["style"]["axis_y"]["mirror"] = json!(true);
                payload_json["resolved"]["style"]["axis_y"]["ticks"]["visible"] = json!(true);
                if let Some(len) = tick_length {
                    payload_json["resolved"]["style"]["axis_y"]["ticks"]["length"] = json!(len);
                }
                let payload = make_payload(payload_json);
                let fonts = test_fonts();
                let svg = render(&payload, &fonts).expect("mirrored columns-only facet renders");
                let width_attr = svg.split("width=\"").nth(1).expect("svg has a width attr");
                width_attr.split('"').next().unwrap().parse::<f64>().expect("width is numeric")
            };
            let default_w = canvas_width(None);
            let widened_w = canvas_width(Some(20.0));
            assert_eq!(
                widened_w - default_w,
                28.0,
                "canvas width grows by the tick_length delta (20.0 - 6.0) on both the ghost's \
                 left edge and the primary's right axis: default {default_w}, widened {widened_w}"
            );
        }

        /// Vega anchors an axis label at `sign * ((tickSize if ticks visible
        /// else 0) + labelPadding)` (`dbt_charts/core/utils.py:853-860`) on
        /// the primary axis exactly as on the ghost. Production's rows-only
        /// facet (`fixtures/multiples/01-rows-only` with
        /// `style.axis_y.ticks.visible: true`) grows 585 -> 591 and moves the
        /// primary label 6px; the columns-only mirrored copy grows 561 -> 573
        /// (ghost band and primary axis each +6) with the ghost label's
        /// absolute x unchanged and the primary's 6px further out.
        #[test]
        fn facet_primary_label_anchors_past_the_visible_tick_length() {
            let render_with = |columns: bool, visible: bool, length: Option<f64>| {
                let mut payload_json = make_facet_payload("shared");
                if columns {
                    payload_json["resolved"]["multiples"] =
                        json!({"rows": null, "columns": "region", "scale": "shared"});
                    payload_json["resolved"]["style"]["axis_y"]["mirror"] = json!(true);
                }
                payload_json["resolved"]["style"]["axis_y"]["ticks"]["visible"] = json!(visible);
                if let Some(len) = length {
                    payload_json["resolved"]["style"]["axis_y"]["ticks"]["length"] = json!(len);
                }
                let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
                let width: f64 = svg
                    .split("width=\"")
                    .nth(1)
                    .unwrap()
                    .split('"')
                    .next()
                    .unwrap()
                    .parse()
                    .unwrap();
                (width, text_xs(&svg, ">1,000<"))
            };
            for (length, tick) in [(None, 6.0), (Some(20.0), 20.0)] {
                let (off_w, off_xs) = render_with(false, false, length);
                let (on_w, on_xs) = render_with(false, true, length);
                assert_eq!(
                    on_w - off_w,
                    tick,
                    "rows-only canvas grows by the primary's tick ({length:?})"
                );
                assert_eq!(
                    on_xs[0] - off_xs[0],
                    tick,
                    "rows-only primary label moves out by the tick ({length:?})"
                );

                let (off_w, off_xs) = render_with(true, false, length);
                let (on_w, on_xs) = render_with(true, true, length);
                assert_eq!(
                    on_w - off_w,
                    2.0 * tick,
                    "mirrored canvas grows by the ghost band and the primary ({length:?})"
                );
                assert_eq!(
                    on_xs[0] - off_xs[0],
                    0.0,
                    "ghost anchor keeps its absolute x ({length:?})"
                );
                assert_eq!(
                    on_xs[1] - off_xs[1],
                    tick,
                    "primary label moves out by the tick ({length:?})"
                );
            }
        }

        /// Every rung of the primary axis, and of its mirror ghost, wears a
        /// tick mark of the tick length: `ticks` carries over to the ghost
        /// verbatim, so labels and marks pair one to one.
        #[test]
        fn facet_axes_paint_a_tick_mark_per_rung_on_the_primary_and_the_ghost() {
            for columns in [false, true] {
                let mut payload_json = make_facet_payload("shared");
                if columns {
                    payload_json["resolved"]["multiples"] =
                        json!({"rows": null, "columns": "region", "scale": "shared"});
                    payload_json["resolved"]["style"]["axis_y"]["mirror"] = json!(true);
                }
                payload_json["resolved"]["style"]["axis_y"]["ticks"] =
                    json!({"color": "#D0D3D9", "visible": true, "count": 6.0, "length": 9.0});
                let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
                let labels = value_label_xs(&svg);
                assert!(!labels.is_empty());
                assert_eq!(tick_marks(&svg, 9.0).len(), labels.len(), "columns={columns}: {svg}");
            }
        }

        /// `mirror_axis.py`'s own contract (`dict(primary_axis)` verbatim
        /// except `orient`/`grid`/`title`) means the ghost's paint must be
        /// the SAME resolved label style the primary paints with, not a
        /// second, independent read of theme defaults. Regression for the
        /// ghost hardcoding `font-size="11"` and `fill="#626366"` and
        /// dropping `font-weight` entirely: with a theme whose resolved
        /// label style is NOT the default (`fill`/`size`/`weight` all set
        /// to values the hardcoded literals cannot match), both the
        /// primary's one painted ladder and the ghost's one repainted copy
        /// must carry the SAME style string.
        #[test]
        fn ghost_labels_paint_with_the_primarys_own_resolved_style() {
            let mut payload_json = make_facet_payload("shared");
            payload_json["resolved"]["multiples"] =
                json!({"rows": null, "columns": "region", "scale": "shared"});
            payload_json["resolved"]["style"]["axis_y"]["mirror"] = json!(true);
            payload_json["resolved"]["style"]["axis_y"]["labels"]["font"]["color"] =
                json!("#123456");
            payload_json["resolved"]["style"]["axis_y"]["labels"]["font"]["size"] = json!(15.0);
            payload_json["resolved"]["style"]["axis_y"]["labels"]["font"]["weight"] = json!(700);
            let payload = make_payload(payload_json);
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("mirrored columns-only facet renders");
            assert_eq!(
                svg.matches("font-size=\"15\" font-weight=\"700\" fill=\"#123456\"").count(),
                10,
                "primary (last column, one per tick) and ghost (opposite edge, the same \
                 ticks repainted) both paint with the SAME resolved label style — \
                 5 value ticks × 2 copies: {svg}"
            );
        }

        /// A grid facet's own row-header band is ADDITIVE — `[title][
        /// HEADER_TITLE_OFFSET][ghost axis band][cell]`. The row title's own
        /// text `x` is invariant to this (it always aligns its right edge to
        /// its own column's right edge, by design — `header_right_edge -
        /// row_label_widths[i]` reduces to `max_row_label_w - label_widths
        /// [i]`, independent of `row_header_offset`), so the OBSERVABLE
        /// effect is the whole row-header COLUMN'S own width: a mirrored
        /// grid's first panel starts far further right than an unmirrored
        /// one, because `margin_left` (`ceil(frame_left_extent)`) carries the ghost's
        /// own large reserve instead of the flat `HEADER_ROW_OFFSET` (2px)
        /// placeholder. Coordinate-asserting regression for `row_header_
        /// offset`'s own law (mutation-checked: reverting `row_header_
        /// offset` to the flat `HEADER_ROW_OFFSET` unconditionally — as if
        /// the ghost were never folded into the row-header band — collapses
        /// both renders' first-panel `x` to nearly the SAME value, failing
        /// this test; confirmed against this exact mutation, then
        /// reverted). Also asserts the ghost's own axis labels (`"1,000"`
        /// etc, painted by the composer's own mirror loop) are present only
        /// when mirrored — the row-facet combination this rung newly ports.
        #[test]
        fn grid_facet_with_mirror_widens_the_row_header_column() {
            let grid_payload = |mirror: bool| {
                let mut p = make_facet_payload("shared");
                p["resolved"]["multiples"] =
                    json!({"rows": "region", "columns": "channel", "scale": "shared"});
                p["resolved"]["panel_axes"] = json!([
                    {"field": "region", "values": ["\"North\"", "\"South\""]},
                    {"field": "channel", "values": ["\"Retail\"", "\"Wholesale\""]},
                ]);
                p["resolved"]["style"]["axis_y"]["mirror"] = json!(mirror);
                p["resolved"]["style"]["axis_y"]["ticks"]["visible"] = json!(true);
                p["rows"] = json!([
                    {"product": "Jan", "region": "North", "channel": "Retail", "revenue": 1200.0},
                    {"product": "Feb", "region": "North", "channel": "Retail", "revenue": 1800.0},
                    {"product": "Jan", "region": "North", "channel": "Wholesale", "revenue": 900.0},
                    {"product": "Feb", "region": "North", "channel": "Wholesale", "revenue": 1400.0},
                    {"product": "Jan", "region": "South", "channel": "Retail", "revenue": 1100.0},
                    {"product": "Feb", "region": "South", "channel": "Retail", "revenue": 1600.0},
                    {"product": "Jan", "region": "South", "channel": "Wholesale", "revenue": 800.0},
                    {"product": "Feb", "region": "South", "channel": "Wholesale", "revenue": 1300.0},
                ]);
                p
            };
            let fonts = test_fonts();
            let unmirrored = render(&make_payload(grid_payload(false)), &fonts).expect("unmirrored grid renders");
            let mirrored = render(&make_payload(grid_payload(true)), &fonts).expect("mirrored grid renders");

            // The primary (last-column) axis paints ">1,000<" regardless of
            // mirror; the ghost repaints the SAME label at the opposite
            // edge only when mirrored, so mirrored has one extra occurrence
            // per row (2 rows here) over the unmirrored count.
            let unmirrored_count = unmirrored.matches(">1,000<").count();
            let mirrored_count = mirrored.matches(">1,000<").count();
            assert_eq!(
                mirrored_count, unmirrored_count + 2,
                "ghost repaints the primary's own labels once per row when mirrored: \
                 unmirrored={unmirrored_count} mirrored={mirrored_count}"
            );

            let first_panel_x = |svg: &str| -> f64 {
                let cell = svg.find("role-scope cell").expect("the panel cell");
                let start = cell + svg[cell..].find("<g transform=\"translate(").expect("a panel group");
                let x_start = start + "<g transform=\"translate(".len();
                let x_end = svg[x_start..].find(',').expect("comma before y") + x_start;
                svg[x_start..x_end].parse().expect("numeric x")
            };
            let unmirrored_x = first_panel_x(&unmirrored);
            let mirrored_x = first_panel_x(&mirrored);
            // `margin_left = ceil(HEADER_TITLE_OFFSET + max_row_label_w +
            // row_header_offset)`, `row_header_offset = ghost_reserve +
            // left_mark_overhang` — `HEADER_TITLE_OFFSET`/`max_row_label_w`/
            // `left_mark_overhang` are the SAME in both renders (same row
            // titles, same font, same band x-scale crisp overhang), so the
            // delta isolates `ghost_reserve` alone (`0.0` unmirrored, the
            // real ghost band mirrored) — both renders' own `margin_left`
            // individually `ceil`s before this subtraction, so `71.0` below
            // is that quantized pin, not a rounded-off approximation of the
            // raw (unceiled) `ghost_reserve` value — do not "fix" it toward
            // a fractional number.
            assert_eq!(
                mirrored_x - unmirrored_x,
                71.0,
                "mirrored grid's first panel ({mirrored_x}) must start `ghost_reserve` right \
                 of the unmirrored one ({unmirrored_x}): \
                 unmirrored={unmirrored}\nmirrored={mirrored}"
            );
        }

        #[test]
        fn chart_level_title_on_a_faceted_chart_paints_once_on_the_root() {
            let mut payload_json = make_facet_payload("shared");
            payload_json["resolved"]["title"] = json!("A title");
            let payload = make_payload(payload_json);
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("titled facet renders");
            assert_eq!(svg.matches(">A title<").count(), 1, "{svg}");
        }

        /// The value-axis title is one axis role at the last column of each
        /// row: a two-column facet paints it once, not once per panel.
        #[test]
        fn value_axis_title_on_a_multi_column_facet_paints_once() {
            let mut payload_json = make_facet_payload("shared");
            payload_json["resolved"]["multiples"] =
                json!({"rows": null, "columns": "region", "scale": "shared"});
            payload_json["resolved"]["style"]["axis_y"]["title"]["visible"] = json!(true);
            let payload = make_payload(payload_json);
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("renders");
            assert_eq!(svg.matches(">Revenue<").count(), 1, "{svg}");
        }

        /// The x of every `<text>` reading `label`.
        fn label_xs(svg: &str, label: &str) -> Vec<f64> {
            let tail = format!(">{label}</text>");
            svg.split("<text x=\"")
                .skip(1)
                .filter(|chunk| chunk.split_once('>').is_some_and(|(_, rest)| rest.starts_with(&tail[1..])))
                .map(|chunk| chunk.split('"').next().unwrap().parse().unwrap())
                .collect()
        }

        /// A left value axis on a multi-column facet is the composer's `row_header`
        /// copy, once per row at the left edge; the panels draw no right axis.
        /// Mutation: paint it in the last column's `row_footer` and the label moves right of the grid.
        #[test]
        fn left_axis_on_a_multi_column_facet_paints_once_in_the_row_header() {
            let mut payload_json = make_facet_payload("shared");
            payload_json["resolved"]["multiples"] =
                json!({"rows": null, "columns": "region", "scale": "shared"});
            payload_json["resolved"]["style"]["axis_y"]["position"] = json!("left");
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            let xs = label_xs(&svg, "1,000");
            assert_eq!(xs.len(), 1, "{svg}");
            assert!(xs[0] < 300.0, "the label sits left of the grid's middle: {xs:?}");
        }

        /// A left value axis paints once per row of a rows-only facet, at the left edge.
        #[test]
        fn left_axis_on_a_rows_only_facet_paints_once_per_row_in_the_row_header() {
            let mut payload_json = make_facet_payload("shared");
            payload_json["resolved"]["style"]["axis_y"]["position"] = json!("left");
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            let xs = label_xs(&svg, "1,000");
            assert_eq!(xs.len(), 2, "{svg}");
            assert!(xs.iter().all(|x| *x < 100.0), "{xs:?}");
        }

        /// A numeric x (`fixtures/facet-panel-axis-title`'s own shape:
        /// `axis_y.position: right`, baked `tick_values`, an authored x
        /// label format) must dispatch through the SAME quantitative-x arm
        /// per panel that `render()`'s own top-level gate tries first for a
        /// single chart — regression for the composer bypassing that arm
        /// selection and rendering a numeric x as a band (one evenly-spaced
        /// slot per distinct value) instead of d3's continuous ticks.
        /// `render_vertical_continuous_x_panel`'s own `PanelGeometry.
        /// raw_right_reach` must read `m.raw_left` (`vega_fit::FitMargins.
        /// raw_left`'s own doc comment), never a flat constant: with
        /// `grid.visible: false` no zero rule fires, so the plot's own ink
        /// reaches no further than `[0, panel_w]` on the right and `raw_
        /// left` is `0.0`, giving `row_footer_overhang = ceil(panel_w) -
        /// panel_w`. Mutation: hardcode `raw_right_reach: 2.0` at this
        /// arm's own `PanelGeometry` construction site (`render_vertical_
        /// continuous_x_panel`) and every assertion below fails (the flat
        /// constant widens the canvas and shifts every label right).
        #[test]
        fn numeric_x_bar_facet_row_footer_reads_the_real_zero_rule_reach_not_a_flat_constant() {
            let xs: Vec<(f64, f64)> = (0..4).map(|h| (h as f64 * 6.0, 100.0 + h as f64)).collect();
            let mut payload_json = make_continuous_payload(&xs);
            payload_json["resolved"]["title"] = json!(null);
            payload_json["resolved"]["style"]["axis_y"]["grid"]["visible"] = json!(false);
            payload_json["resolved"]["multiples"] =
                json!({"rows": "region", "columns": null, "scale": "shared"});
            payload_json["resolved"]["panel_axes"] =
                json!([{"field": "region", "values": ["\"North\"", "\"South\""]}]);
            payload_json["rows"] = json!(
                xs.iter()
                    .flat_map(|(x, y)| vec![
                        json!({"product": x, "region": "North", "revenue": y}),
                        json!({"product": x, "region": "South", "revenue": y * 0.5}),
                    ])
                    .collect::<Vec<_>>()
            );
            let payload = make_payload(payload_json);
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("renders");
            assert!(
                svg.starts_with(
                    r#"<svg xmlns="http://www.w3.org/2000/svg" width="581" height="433" viewBox="0 0 581 433">"#
                ),
                "canvas width reserves the real (zero) zero-rule reach, not a flat constant: {svg}"
            );
            assert!(
                svg.contains(r#"x="511.173" y="185.000" text-anchor="end""#) && svg.contains(">0<"),
                "value-axis label anchors at `ceil(panel_w + raw_left) - panel_w` off the real \
                 (zero) reach, not a flat constant: {svg}"
            );
        }
        #[test]
        fn numeric_x_facet_routes_through_the_quantitative_x_arm() {
            let xs: Vec<(f64, f64)> = (0..4).map(|h| (h as f64 * 6.0, 100.0 + h as f64)).collect();
            let mut payload_json = make_continuous_payload(&xs);
            payload_json["resolved"]["title"] = json!(null);
            payload_json["resolved"]["multiples"] =
                json!({"rows": "region", "columns": null, "scale": "shared"});
            payload_json["resolved"]["panel_axes"] =
                json!([{"field": "region", "values": ["\"North\"", "\"South\""]}]);
            payload_json["rows"] = json!(
                xs.iter()
                    .flat_map(|(x, y)| vec![
                        json!({"product": x, "region": "North", "revenue": y}),
                        json!({"product": x, "region": "South", "revenue": y * 0.5}),
                    ])
                    .collect::<Vec<_>>()
            );
            let payload = make_payload(payload_json);
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("renders");
            assert!(svg.contains(">5</text>"), "d3 tick labels, not band labels: {svg}");
            assert!(!svg.contains(">18</text>"), "no per-value band label: {svg}");
        }

        /// End-to-end regression for the composer's own height-correction
        /// wiring (`correct_facet_child_extent`, `facet_compose.rs`),
        /// exercised on a 7-row facet shaped like `dbt-charts/tests/visual/
        /// fixtures/facet-panel-axis-title.yml` (numeric x, `axis_y.
        /// position: right`, baked `tick_values`, 7 `multiples.rows`
        /// panels) at that fixture's own real production slot (600×802).
        /// This composer's row/axis geometry is its own (not vl-convert's),
        /// so its own probed overshoot differs from production's real
        /// `191.0` — `facet_compose.rs`'s own `correct_facet_child_extent_tests`
        /// pins PRODUCTION's own numbers directly; this test instead proves the
        /// wiring: disabling the correction (temporarily hardcoding
        /// `correct_facet_child_extent` to always return `naive`) makes
        /// this assertion FAIL — confirmed by running that exact mutation
        /// against this exact test before reverting it — so the pinned
        /// canvas height below is provably the CORRECTED one, not the
        /// naive `802.0/7.0` guess surviving uncorrected.
        #[test]
        fn seven_row_numeric_x_facet_height_reflects_the_probed_correction() {
            let xs: Vec<(f64, f64)> = (0..4).map(|h| (h as f64 * 6.0, 5.0 + h as f64 * 3.0)).collect();
            let mut payload_json = make_continuous_payload(&xs);
            payload_json["resolved"]["title"] = json!(null);
            payload_json["resolved"]["y_label"] = json!("commits");
            payload_json["resolved"]["multiples"] =
                json!({"rows": "year", "columns": null, "scale": "shared"});
            let years: Vec<&str> =
                vec!["2019", "2020", "2021", "2022", "2023", "2024", "2025"];
            payload_json["resolved"]["panel_axes"] = json!([{
                "field": "year",
                "values": years.iter().map(|y| format!("\"{y}\"")).collect::<Vec<_>>(),
            }]);
            payload_json["rows"] = json!(
                years
                    .iter()
                    .flat_map(|y| xs.iter().map(move |(x, val)| json!({"product": x, "year": y, "revenue": val})))
                    .collect::<Vec<_>>()
            );
            payload_json["width"] = json!(600.0);
            payload_json["height"] = json!(802.0);
            let payload = make_payload(payload_json);
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("seven-row facet renders");
            let dims = svg
                .split_once("viewBox=\"0 0 ")
                .and_then(|(_, rest)| rest.split_once('"'))
                .expect("viewBox present")
                .0
                .to_string();
            assert_eq!(dims, "582 799", "canvas dims reflect the corrected (not naive) child_height: {svg}");
        }

        /// TDD pin for the composite's own outer top-margin reserve
        /// (`render_facet`'s `margin_top = header_band.max(top_axis_
        /// overhang).ceil()`, mirroring real Vega's `ceil(-viewBounds.y1)`
        /// — `correct_facet_child_extent`'s own doc comment carries the
        /// `ViewLayout.js`/scenegraph citation). Same payload as `seven_
        /// row_numeric_x_facet_height_reflects_the_probed_correction`
        /// above; this test isolates the FIRST panel's own translate,
        /// where the reserve actually lands. Before this reserve existed,
        /// the first panel painted at `translate(53.00,16.00)`; disabling
        /// it (temporarily hardcoding `top_axis_overhang` to `0.0` on both
        /// `PanelGeometry` construction sites) reproduces that exact
        /// number and fails this assertion — confirmed by running that
        /// exact mutation against this exact test before reverting it.
        #[test]
        fn seven_row_numeric_x_facet_reserves_the_composite_top_margin() {
            let xs: Vec<(f64, f64)> = (0..4).map(|h| (h as f64 * 6.0, 5.0 + h as f64 * 3.0)).collect();
            let mut payload_json = make_continuous_payload(&xs);
            payload_json["resolved"]["title"] = json!(null);
            payload_json["resolved"]["y_label"] = json!("commits");
            // `make_continuous_payload`'s own baked `tick_values` ([0, 100,
            // 200, 300]) survive the `[y_min, y_max]` filter only when
            // `domain_max` is ALSO baked to 300 — this test's own small
            // `revenue` values (5..14) would otherwise nice `y_max` down
            // to their own data range, filtering out every tick past `0`
            // and leaving no topmost tick to overhang at all (confirmed:
            // without this line, `probe[0].1.top_axis_overhang == 0.0`).
            payload_json["resolved"]["style"]["axis_y"]["domain_max"] = json!(300.0);
            payload_json["resolved"]["multiples"] =
                json!({"rows": "year", "columns": null, "scale": "shared"});
            let years: Vec<&str> =
                vec!["2019", "2020", "2021", "2022", "2023", "2024", "2025"];
            payload_json["resolved"]["panel_axes"] = json!([{
                "field": "year",
                "values": years.iter().map(|y| format!("\"{y}\"")).collect::<Vec<_>>(),
            }]);
            payload_json["rows"] = json!(
                years
                    .iter()
                    .flat_map(|y| xs.iter().map(move |(x, val)| json!({"product": x, "year": y, "revenue": val})))
                    .collect::<Vec<_>>()
            );
            payload_json["width"] = json!(600.0);
            payload_json["height"] = json!(802.0);
            let mut payload = make_payload(payload_json);
            payload.measuring = true;
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("seven-row facet renders");
            assert!(
                svg.contains(r#"<g transform="translate(51.00,22.00)">"#),
                "first panel's own translate reflects the composite's own top-margin reserve \
                 (16 base + 6 reserve, canvas-edge frame — `canvas_x` subtracts the continuous-x \
                 arm's own `left_mark_overhang`, 2.0): {svg}"
            );
        }

        /// The topmost tick's own y-position scales with panel height
        /// whenever it sits BELOW the domain max (production bakes
        /// headroom — `19 × 1.08 = 20.52` on the real `facet-panel-
        /// axis-title` fixture) — so the overhang measured at the NAIVE
        /// probe height is the wrong term to reserve in the FINAL,
        /// corrected-height canvas; production's own real Vega render
        /// re-lays-out fresh at the corrected height and its own top
        /// margin reflects THAT height. Reproduced on the real compiled
        /// `facet-panel-axis-title` spec with `axis_y.scale.domainMax`
        /// patched to `20.62` (this test's own `domain_max`), via a live
        /// `vega@6.2.0` scenegraph probe (`vl_convert.vegalite_to_vega` on
        /// the spy-captured, already-height-corrected VL spec): at the
        /// port's own final child height (`87.28571428571428`) `row_
        /// footer`'s own `bounds.y1 = -3.375502286268525` → top `4`; at the
        /// naive probe height (`114.57142857142857`) `bounds.y1 =
        /// -2.5550782873770164` → top `3` — the two heights straddle an
        /// integer, so using the wrong one is a real, visible 1px error,
        /// not a rounding curiosity. This test's OWN 7-row payload (same
        /// shape as the test above, this port's own font metrics rather
        /// than production's) pins the same class of height-dependence,
        /// mutation-checked: reading `probe[0].1.top_axis_overhang` here
        /// instead of `final_panels[0].1.top_axis_overhang` fails this
        /// exact assertion.
        #[test]
        fn seven_row_numeric_x_facet_top_margin_uses_final_height_not_probe_height() {
            let xs: Vec<(f64, f64)> = (0..4).map(|h| (h as f64 * 6.0, 5.0 + h as f64 * 3.0)).collect();
            let mut payload_json = make_continuous_payload(&xs);
            payload_json["resolved"]["title"] = json!(null);
            payload_json["resolved"]["y_label"] = json!("commits");
            payload_json["resolved"]["style"]["axis_y"]["tick_values"] =
                json!([0.0, 5.0, 10.0, 15.0, 20.0]);
            payload_json["resolved"]["style"]["axis_y"]["domain_max"] = json!(20.62);
            payload_json["resolved"]["multiples"] =
                json!({"rows": "year", "columns": null, "scale": "shared"});
            let years: Vec<&str> =
                vec!["2019", "2020", "2021", "2022", "2023", "2024", "2025"];
            payload_json["resolved"]["panel_axes"] = json!([{
                "field": "year",
                "values": years.iter().map(|y| format!("\"{y}\"")).collect::<Vec<_>>(),
            }]);
            payload_json["rows"] = json!(
                years
                    .iter()
                    .flat_map(|y| xs.iter().map(move |(x, val)| json!({"product": x, "year": y, "revenue": val})))
                    .collect::<Vec<_>>()
            );
            payload_json["width"] = json!(600.0);
            payload_json["height"] = json!(802.0);
            let payload = make_payload(payload_json);
            let fonts = test_fonts();
            let svg = render(&payload, &fonts).expect("seven-row facet renders");
            assert!(
                svg.contains(r#"<g transform="translate(51.00,20.00)">"#),
                "first panel's own translate reflects the FINAL child height's own top-margin \
                 reserve, not the naive probe height's (canvas-edge frame — `canvas_x` subtracts \
                 the continuous-x arm's own `left_mark_overhang`, 2.0): {svg}"
            );
        }

        /// A continuous x facet paints the shared value ladder once per row (the
        /// last column) and the mirror ghost repeats it on the left edge. The
        /// fixtures `bar-columns-continuous-x` and `bar-rows-mirror-continuous-x`
        /// pin the geometry against production.
        #[test]
        fn a_continuous_x_facet_paints_its_ladder_and_the_ghost_repeats_it() {
            let xs: Vec<(f64, f64)> = (0..4).map(|h| (h as f64 * 6.0, 100.0 + h as f64)).collect();
            let ladder_labels = |multiples: serde_json::Value, mirror: bool| {
                let mut payload_json = make_continuous_payload(&xs);
                payload_json["resolved"]["title"] = json!(null);
                payload_json["resolved"]["style"]["axis_y"]["mirror"] = json!(mirror);
                payload_json["resolved"]["multiples"] = multiples;
                payload_json["resolved"]["panel_axes"] =
                    json!([{"field": "region", "values": ["\"North\"", "\"South\""]}]);
                payload_json["rows"] = json!(
                    xs.iter()
                        .flat_map(|(x, y)| vec![
                            json!({"product": x, "region": "North", "revenue": y}),
                            json!({"product": x, "region": "South", "revenue": y * 0.5}),
                        ])
                        .collect::<Vec<_>>()
                );
                let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
                svg.matches("text-anchor=\"end\"").count()
            };
            for multiples in [
                json!({"rows": null, "columns": "region", "scale": "shared"}),
                json!({"rows": "region", "columns": null, "scale": "shared"}),
            ] {
                let plain = ladder_labels(multiples.clone(), false);
                let mirrored = ladder_labels(multiples.clone(), true);
                assert!(plain > 0, "{multiples}: the ladder paints");
                assert!(mirrored > plain, "{multiples}: the ghost repeats the ladder");
            }
        }

        /// The continuous-x ghost's tick marks follow the band arm's gate: none
        /// when the ticks are hidden (a zero-length `<line>` per rung otherwise),
        /// one per rung, ghost included, when they show.
        #[test]
        fn a_continuous_x_ghost_paints_tick_marks_only_when_ticks_show() {
            let xs: Vec<(f64, f64)> = (0..4).map(|h| (h as f64 * 6.0, 100.0 + h as f64)).collect();
            let render_ticks = |ticks: serde_json::Value| {
                let mut payload_json = make_continuous_payload(&xs);
                payload_json["resolved"]["title"] = json!(null);
                payload_json["resolved"]["style"]["axis_y"]["mirror"] = json!(true);
                payload_json["resolved"]["style"]["axis_y"]["ticks"] = ticks;
                payload_json["resolved"]["multiples"] = json!({"rows": null, "columns": "region", "scale": "shared"});
                payload_json["resolved"]["panel_axes"] =
                    json!([{"field": "region", "values": ["\"North\"", "\"South\""]}]);
                payload_json["rows"] = json!(
                    xs.iter()
                        .flat_map(|(x, y)| vec![
                            json!({"product": x, "region": "North", "revenue": y}),
                            json!({"product": x, "region": "South", "revenue": y * 0.5}),
                        ])
                        .collect::<Vec<_>>()
                );
                render(&make_payload(payload_json), &test_fonts()).expect("renders")
            };
            let hidden = render_ticks(json!({"visible": false, "count": 6.0, "length": 0.0}));
            let empty: Vec<_> =
                horizontal_lines(&hidden).into_iter().filter(|(x1, x2, _)| (x2 - x1).abs() < 1e-6).collect();
            assert!(empty.is_empty(), "no zero-length tick line: {empty:?}");
            let shown = render_ticks(json!({"visible": true, "color": "#D0D3D9", "count": 6.0, "length": 9.0}));
            assert_eq!(tick_marks(&shown, 9.0).len(), 4 * 2, "a mark per rung on the primary and the ghost");
        }

        /// A mirror's own `format` relabels only the ghost (`mirror_axis.py`): the
        /// primary keeps its ladder and the ghost paints the same rungs in its format.
        #[test]
        fn mirror_object_form_relabels_the_ghost() {
            let mut payload_json = make_facet_payload("shared");
            payload_json["resolved"]["multiples"] =
                json!({"rows": null, "columns": "region", "scale": "shared"});
            payload_json["resolved"]["style"]["axis_y"]["mirror"] = json!({"format": "$,.0f"});
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert_eq!(svg.matches(">1,000<").count(), 1, "the primary ladder: {svg}");
            assert_eq!(svg.matches(">$1,000<").count(), 1, "the ghost's own format: {svg}");
        }

        /// The production wire shape of `axis_y.mirror` (`ResolvedMirrorAxis`,
        /// `axis_cascade.py:708-713`) for an authored `mirror: true`: every
        /// field null. It is the same request as the bare `true` and must
        /// render the identical mirrored facet, ghost included.
        #[test]
        fn production_mirror_true_object_renders_like_bare_true() {
            let render_with = |mirror: Value| {
                let mut payload_json = make_facet_payload("shared");
                payload_json["resolved"]["multiples"] =
                    json!({"rows": null, "columns": "region", "scale": "shared"});
                payload_json["resolved"]["style"]["axis_y"]["mirror"] = mirror;
                render(&make_payload(payload_json), &test_fonts())
                    .expect("mirror: true renders on the multiples arm")
            };
            let production = render_with(
                json!({"format": null, "expr": null, "ruler": null, "tick_label": null}),
            );
            assert_eq!(production, render_with(json!(true)));
            assert_eq!(
                production.matches(">1,000<").count(),
                2,
                "primary ladder once plus the ghost's repaint: {production}"
            );
        }

        /// A `ResolvedMirrorAxis` with `expr` speaks the ghost's labels through the expression;
        /// the primary keeps its own.
        #[test]
        fn mirror_object_with_expr_relabels_the_ghost() {
            let mut payload_json = make_facet_payload("shared");
            payload_json["resolved"]["style"]["axis_y"]["mirror"] =
                json!({"format": null, "expr": "datum.label + '%'", "ruler": null, "tick_label": null});
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert!(svg.contains("%</text>"), "the ghost labels carry the expression: {svg}");
        }

        /// A compacting ruler production bakes on a right-edge measure axis.
        fn compacting_ruler() -> Value {
            json!({
                "exponent": 3, "mode": "repeat", "register": "narrative", "digit_spec": ",.1~f",
                "anchor_at_start": true, "reservation": "", "prefix": ""
            })
        }

        /// Production builds `mirror.ruler` for the ghost's own edge
        /// (`axis_cascade.py:693-713`) and the ghost's `labelExpr` is composed
        /// from it (`features/mirror_axis.py:224-228`); on a right-edge primary
        /// that anchors identically it equals `axis_y.ruler`, so the ghost
        /// paints the primary's labels and the request renders exactly as a
        /// bare `true` does, compacted on both edges.
        #[test]
        fn mirror_object_with_the_primarys_own_ruler_renders_like_bare_true() {
            let render_with = |mirror: Value| {
                let mut payload_json = make_facet_payload("shared");
                payload_json["resolved"]["multiples"] =
                    json!({"rows": null, "columns": "region", "scale": "shared"});
                payload_json["resolved"]["style"]["axis_y"]["ruler"] = compacting_ruler();
                payload_json["resolved"]["style"]["axis_y"]["mirror"] = mirror;
                render(&make_payload(payload_json), &test_fonts())
                    .expect("a mirror ruler equal to the primary's renders")
            };
            let equal = render_with(json!({
                "format": null,
                "expr": null,
                "ruler": compacting_ruler(),
                "tick_label": null,
            }));
            assert_eq!(equal, render_with(json!(true)));
            assert_eq!(
                equal.matches(">1k<").count(),
                2,
                "primary ladder once plus the ghost's repaint, both compacted: {equal}"
            );
        }

        /// A ghost ruler that is not the primary's is the ruler baked for the ghost's own
        /// edge: the ghost paints through it. A ghost with no ruler of its own repeats the
        /// primary's labels.
        #[test]
        fn mirror_object_with_a_different_ruler_paints_the_ghost_through_it() {
            let render_with = |primary: Value, mirror_ruler: Value| {
                let mut payload_json = make_facet_payload("shared");
                payload_json["resolved"]["multiples"] =
                    json!({"rows": null, "columns": "region", "scale": "shared"});
                payload_json["resolved"]["style"]["axis_y"]["ruler"] = primary;
                payload_json["resolved"]["style"]["axis_y"]["mirror"] = json!(
                    {"format": null, "expr": null, "ruler": mirror_ruler, "tick_label": null}
                );
                render(&make_payload(payload_json), &test_fonts()).expect("renders")
            };
            let unruled_primary = render_with(Value::Null, compacting_ruler());
            assert_eq!(unruled_primary.matches(">1,000<").count(), 1, "{unruled_primary}");
            assert_eq!(unruled_primary.matches(">1k<").count(), 1, "{unruled_primary}");
            let ruled_primary = render_with(compacting_ruler(), Value::Null);
            assert_eq!(ruled_primary.matches(">1k<").count(), 2, "the ghost repeats the primary: {ruled_primary}");
        }

        /// A mirror value that is neither a bool nor a `ResolvedMirrorAxis`
        /// object is a contract violation, not a request to mirror.
        #[test]
        fn mirror_object_with_a_key_outside_resolved_mirror_axis_refuses() {
            let mut payload_json = make_facet_payload("shared");
            payload_json["resolved"]["style"]["axis_y"]["mirror"] =
                json!({"format": null, "expr": null, "ruler": null, "tick_label": null, "side": "left"});
            let err = render(&make_payload(payload_json), &test_fonts()).unwrap_err();
            assert_eq!(
                err,
                "bar: axis_y.mirror carries \"side\", which is not a ResolvedMirrorAxis field"
            );
        }

        #[test]
        fn mirror_of_an_unknown_shape_refuses() {
            let mut payload_json = make_facet_payload("shared");
            payload_json["resolved"]["style"]["axis_y"]["mirror"] = json!("both");
            let err = render(&make_payload(payload_json), &test_fonts()).unwrap_err();
            assert_eq!(
                err,
                "bar: axis_y.mirror \"both\" is not a valid shape — expected true, false, null, \
                 or a resolved mirror object"
            );
        }
    }

    /// `ResolvedChart.link` (`automatic_link.py`'s filter/entity-drill template) —
    /// wired only on the plain single-series vertical band-scale bar; every
    /// other shape refuses by name (`render()`'s own entry gate, plus the
    /// temporal/continuous-x/multiples gates each fires from).
    mod automatic_link_tests {
        use super::*;
        use serde_json::json;

        fn linked_payload(cats: &[(&str, f64)], link: &str) -> Payload {
            let mut payload_json = make_vertical_payload(cats, false);
            payload_json["resolved"]["link"] = json!(link);
            make_payload(payload_json)
        }

        /// `resolve_column_set_link`'s own entity-drill shape
        /// (`row_detail=False`): one `<a href>` per bar, keyed by that bar's
        /// own `x` (category) value.
        #[test]
        fn single_series_bar_emits_one_href_per_datum() {
            let payload = linked_payload(
                &[("owner_001", 50000.0), ("owner_002", 75000.0)],
                "/data/db/main/owner/detail/?owner_id={{ x }}",
            );
            let svg = render(&payload, &test_fonts()).expect("a chart-root link must render");
            assert!(
                svg.contains("<a href=\"/data/db/main/owner/detail/?owner_id=owner_001\">"),
                "first datum's own href missing:\n{svg}"
            );
            assert!(
                svg.contains("<a href=\"/data/db/main/owner/detail/?owner_id=owner_002\">"),
                "second datum's own href missing:\n{svg}"
            );
        }

        /// A non-page link stamps every bar anchor, a new-tab one with its
        /// target; page and missing stamp nothing.
        #[test]
        fn a_link_mode_stamps_only_non_page_bar_anchors() {
            let render_with = |mode: Option<&str>| {
                let mut payload_json = make_vertical_payload(&[("a", 1000.0), ("b", 2000.0)], false);
                payload_json["resolved"]["link"] = json!("/x/?r={{ x }}");
                if let Some(mode) = mode {
                    payload_json["resolved"]["link_open"] = json!(mode);
                }
                render(&make_payload(payload_json), &test_fonts())
            };
            let page = render_with(None).unwrap();
            assert_eq!(render_with(Some("page")).unwrap(), page);
            assert!(!page.contains("data-dbt-link-open"), "{page}");
            for (mode, target) in [("modal", ""), ("preview", ""), ("new_tab", " target=\"_blank\"")] {
                let svg = render_with(Some(mode)).unwrap();
                let first = format!("<a href=\"/x/?r=a\" data-dbt-link-open=\"{mode}\"{target}>");
                assert_eq!(svg.matches(&first).count(), 1, "{svg}");
                assert_eq!(svg.matches(&format!("data-dbt-link-open=\"{mode}\"")).count(), 2, "{svg}");
            }
            assert!(render_with(Some("popup")).unwrap_err().contains("popup"));
        }

        /// The host's link context rewrites a bar's board link like it does a table cell's.
        #[test]
        fn a_bar_link_is_rewritten_through_the_host_link_context() {
            let mut payload_json = make_vertical_payload(&[("a", 1.0), ("b", 2.0)], false);
            payload_json["resolved"]["link"] = json!("/target?label={{ x }}");
            payload_json["resolved"]["link_open"] = json!("modal");
            payload_json["link_context"] = json!({"root": "/org/proj/d", "current_board_slug": "home", "origin": ""});
            let svg = render(&make_payload(payload_json), &test_fonts()).unwrap();
            assert!(svg.contains("<a href=\"/org/proj/d/target?label=a\" data-dbt-link-open=\"modal\">"), "{svg}");
        }

        /// A chart with no `link:` spec at all draws no wrapper — the
        /// feature is opt-in, never a default.
        #[test]
        fn no_link_spec_emits_no_wrapper() {
            let mut payload_json = make_vertical_payload(&[("owner_001", 50000.0)], false);
            payload_json["resolved"]["link"] = Value::Null;
            let payload = make_payload(payload_json);
            let svg = render(&payload, &test_fonts()).unwrap();
            assert!(!svg.contains("<a href="), "no href expected with no link spec:\n{svg}");
        }

        /// `_vega_encode_field`'s own replace() chain: a category value with
        /// a space and an `&` percent-encodes safely into the query string —
        /// matching `test_bar_chart_two_plain_text_dims_produces_encoded_
        /// filter_link`'s own production shape, ported here as a unit test.
        #[test]
        fn a_special_character_category_is_percent_encoded() {
            let payload =
                linked_payload(&[("A&B C", 1000.0)], "/data/db/main/x/?region={{ x }}");
            let svg = render(&payload, &test_fonts()).unwrap();
            assert!(
                svg.contains("<a href=\"/data/db/main/x/?region=A%26B%20C\">"),
                "urlencoded href missing:\n{svg}"
            );
        }

        /// The corpus row `examples/drill-down_0/by_status`: an authored
        /// `{{ x }}` link on a horizontal single-series bar. Vega's own
        /// structure puts the `<a>` INSIDE the `.role-mark` group, wrapping
        /// only the datum's first drawn element (the aria-labeled fill).
        #[test]
        fn horizontal_bar_wraps_the_datum_fill_inside_its_mark_group() {
            let mut payload_json = make_horizontal_payload();
            payload_json["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
            payload_json["resolved"]["link"] =
                json!("/customer-support/tickets/list?status={{ x }}");
            let payload = make_payload(payload_json);
            let svg = render(&payload, &test_fonts()).expect("a horizontal link must render");
            let a = svg.find("<a href=\"/customer-support/tickets/list?status=").expect("anchor");
            let group = svg[..a].rfind("<g class=\"mark-rect role-mark").expect("mark group");
            assert!(!svg[group..a].contains("</g>"), "anchor must sit inside the mark group:\n{svg}");
            let close = a + svg[a..].find("</a>").expect("anchor closes");
            assert!(
                svg[a..close].matches("<path").count() + svg[a..close].matches("<rect").count() == 1,
                "anchor wraps exactly the first drawn element:\n{svg}"
            );
        }

        /// A modal link stamps every horizontal bar anchor; page stamps nothing.
        #[test]
        fn a_link_mode_stamps_only_modal_horizontal_bar_anchors() {
            let render_with = |mode: &str| {
                let mut payload_json = make_horizontal_payload();
                payload_json["resolved"]["link"] = json!("/x/?s={{ x }}");
                payload_json["resolved"]["link_open"] = json!(mode);
                render(&make_payload(payload_json), &test_fonts()).unwrap()
            };
            let page = render_with("page");
            assert!(page.contains("<a href=\"/x/?s="), "{page}");
            assert!(!page.contains("data-dbt-link-open"), "{page}");
            let modal = render_with("modal");
            assert_eq!(modal.matches("<a href=").count(), modal.matches("data-dbt-link-open=\"modal\"").count(), "{modal}");
            assert!(modal.contains("data-dbt-link-open=\"modal\""), "{modal}");
        }

        /// `_build_href_calc_expr` applies `timeFormat` to a temporal x, which a
        /// band-scale date string makes an invalid date: d3 pads each NaN part.
        #[test]
        fn a_temporal_x_with_a_link_reads_the_invalid_date_timeformat() {
            let mut payload_json = make_vertical_payload(&[("2025-01-01", 100.0), ("2025-02-01", 90.0)], false);
            payload_json["resolved"]["link"] = json!("/data/db/main/x/?d={{ x }}");
            let svg = render(&make_payload(payload_json), &test_fonts()).unwrap();
            assert_eq!(hrefs(&svg), ["/data/db/main/x/?d=0NaN-NaN-NaN"; 2], "{svg}");
        }

        /// A `{{ y }}` placeholder reads the datum's own measure.
        #[test]
        fn a_y_placeholder_reads_the_datum_measure() {
            let payload = linked_payload(&[("Jan", 1000.0)], "/data/x/?v={{ y }}");
            let svg = render(&payload, &test_fonts()).unwrap();
            assert_eq!(hrefs(&svg), ["/data/x/?v=1000"], "{svg}");
        }

        fn linked(payload_json: serde_json::Value) -> String {
            render(&make_payload(payload_json), &test_fonts()).unwrap()
        }

        fn linked_json(cats: &[(&str, f64)], link: &str) -> serde_json::Value {
            let mut payload_json = make_vertical_payload(cats, false);
            payload_json["resolved"]["link"] = json!(link);
            payload_json
        }

        fn hrefs(svg: &str) -> Vec<&str> {
            svg.split("<a href=\"").skip(1).map(|s| s.split('"').next().unwrap()).collect()
        }

        /// A color-bound bar links each series' own datum, `{{ color }}` included.
        #[test]
        fn a_color_bound_bar_links_each_series_datum() {
            let mut payload_json = make_vertical_payload(&[("Jan", 100.0), ("Feb", 150.0)], true);
            payload_json["resolved"]["link"] = json!("/x/?r={{ x }}&c={{ color }}");
            let svg = linked(payload_json);
            let links = hrefs(&svg);
            assert!(!links.is_empty() && links.iter().all(|h| h.contains("&amp;c=")), "{svg}");
            assert!(links.iter().collect::<std::collections::HashSet<_>>().len() == links.len(), "{svg}");
        }

        /// A stacked bar links every segment.
        #[test]
        fn a_stacked_bar_links_every_segment() {
            let mut payload_json = linked_json(&[("Jan", 100.0), ("Feb", 150.0)], "/x/?r={{ x }}");
            payload_json["resolved"]["stack"] = json!("zero");
            assert_eq!(hrefs(&linked(payload_json)), ["/x/?r=Jan", "/x/?r=Feb"]);
        }

        fn with_layer(layer: serde_json::Value) -> serde_json::Value {
            let mut payload_json = linked_json(&[("Jan", 100.0), ("Feb", 150.0)], "/x/?r={{ x }}");
            payload_json["rows"] = json!([
                {"product": "Jan", "revenue": 100.0, "target": 90.0},
                {"product": "Feb", "revenue": 150.0, "target": 140.0},
            ]);
            payload_json["resolved"]["layers"] = json!([layer]);
            payload_json["resolved"]["legend"]["visible"] = json!(true);
            payload_json
        }

        /// A layer over the chart's own rows carries the chart's link on its marks.
        #[test]
        fn a_layer_on_the_charts_rows_links_its_marks() {
            for layer in [layers::bar_layer_json(None), line_layer_json("#dea628")] {
                let svg = linked(with_layer(layer));
                let links = hrefs(&svg);
                assert!(links.len() > 2 && links.iter().all(|h| *h == "/x/?r=Jan" || *h == "/x/?r=Feb"), "{links:?}");
            }
        }

        /// A support table's source cells read their category's datum.
        #[test]
        fn a_support_table_links_its_source_cells() {
            let mut payload_json = linked_json(&[("Alpha", 1000.0), ("Beta", 2000.0)], "/x/?r={{ x }}");
            payload_json["resolved"]["support_table"] = json!({
                "entries": [{"source": "revenue", "format": {"spec": "$,.0f", "raw": "currency_whole"}, "label": "Revenue"}]
            });
            payload_json["resolved"]["effective_support_table_style"] = support_table_style_json();
            let svg = linked(payload_json);
            let links = hrefs(&svg);
            assert!(links.len() > 2 && links.contains(&"/x/?r=Alpha") && links.contains(&crate::href::UNDEFINED_HREF), "{links:?}");
        }

        #[test]
        fn a_quantitative_x_bar_with_a_link_draws_one_href_per_bar() {
            let mut payload_json = make_continuous_payload(&[(1.0, 100.0), (2.0, 200.0), (4.0, 50.0)]);
            payload_json["resolved"]["link"] = json!("/x/?r={{ x }}");
            let svg = render(&make_payload(payload_json), &test_fonts()).unwrap();
            assert_eq!(hrefs(&svg), ["/x/?r=1", "/x/?r=2", "/x/?r=4"], "{svg}");
        }

        /// Each panel draws its own anchor per category, reading its own row.
        #[test]
        fn a_multiples_bar_with_a_link_draws_one_href_per_panel_bar() {
            let mut payload_json = facet::make_facet_payload("shared");
            payload_json["resolved"]["link"] = json!("/x/?r={{ x }}&v={{ y }}");
            let svg = render(&make_payload(payload_json), &test_fonts()).unwrap();
            assert_eq!(
                hrefs(&svg),
                [
                    "/x/?r=Jan&amp;v=1200",
                    "/x/?r=Feb&amp;v=1800",
                    "/x/?r=Mar&amp;v=2400",
                    "/x/?r=Jan&amp;v=900",
                    "/x/?r=Feb&amp;v=1500",
                    "/x/?r=Mar&amp;v=2100",
                ],
                "{svg}"
            );
        }

        /// A horizontal band category reads its datum verbatim: the date
        /// string is no `timeFormat` input.
        #[test]
        fn a_temporal_x_on_a_horizontal_bar_with_a_link_reads_the_datum_text() {
            let mut payload_json = make_horizontal_payload();
            payload_json["rows"] = json!([
                {"product": "2025-01-01", "revenue": 800.0},
                {"product": "2025-02-01", "revenue": 600.0},
            ]);
            payload_json["resolved"]["link"] = json!("/x/?d={{ x }}");
            let svg = render(&make_payload(payload_json), &test_fonts()).unwrap();
            assert_eq!(hrefs(&svg), ["/x/?d=2025-01-01", "/x/?d=2025-02-01"], "{svg}");
        }

        /// A bucketed `YYYY-MM` x is not a standard date to the type inference,
        /// so the href is the percent-encoded bucket-start datum, not `timeFormat`.
        #[test]
        fn a_bucketed_month_x_with_a_link_reads_the_bucket_start_date() {
            let mut payload_json = make_vertical_payload(&[("2025-01", 100.0), ("2025-02", 90.0)], false);
            payload_json["resolved"]["link"] = json!("/x/?d={{ x }}");
            let svg = render(&make_payload(payload_json), &test_fonts()).unwrap();
            assert_eq!(hrefs(&svg), ["/x/?d=2025-01-01", "/x/?d=2025-02-01"], "{svg}");
        }

        /// The `re.split` law: an opener with no closer stays literal, and a
        /// match may start at any `{{` — an earlier failed opener (`{{ {{ x }}`,
        /// `{{{ x }}`) does not consume it.
        #[test]
        fn a_malformed_opener_stays_literal_and_never_hides_a_later_placeholder() {
            for (template, expected) in [
                ("/a/?v={{ x }}&w={{", "/a/?v=q&amp;w={{"),
                ("/a/{{ x", "/a/{{ x"),
                ("/a/{{ {{ x }}", "/a/{{ q"),
                ("/a/{{{ x }}", "/a/{q"),
                ("/a/{{x}}{{\tx\n}}", "/a/qq"),
                ("/a/{{ X }}{{ xy }}{{ category }}", "/a/{{ X }}{{ xy }}{{ category }}"),
            ] {
                let svg = render(&make_payload(linked_json(&[("q", 1000.0)], template)), &test_fonts())
                    .expect("renders");
                assert_eq!(hrefs(&svg), [expected], "{template}");
            }
        }

        /// JS `'' + null` is `"null"`: a null category links `?status=null`,
        /// where an empty-string one links `?status=`.
        #[test]
        fn a_null_category_links_the_js_null_text() {
            let mut payload_json = linked_json(&[("A", 1000.0)], "/x/?status={{ x }}");
            payload_json["rows"] = json!([
                {"product": null, "revenue": 1000.0},
                {"product": "B", "revenue": 1200.0},
            ]);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
            assert_eq!(hrefs(&svg), ["/x/?status=null", "/x/?status=B"], "{svg}");
        }

        /// The first arc radius an emitted bar path carries.
        fn arc_radius(svg: &str) -> f64 {
            let at = svg.find("A ").unwrap_or_else(|| panic!("no arc in {svg}")) + 2;
            svg[at..].split(',').next().unwrap().parse().unwrap()
        }

        /// A corner takes at most a quarter of the bar's thickness (cross
        /// axis) and half its length, in both orientations.
        #[test]
        fn a_bar_corner_is_capped_by_its_thickness_not_its_length() {
            let drawn = |horizontal: bool, w: f64, h: f64, r: f64| {
                print::print(
                    &bar_passes(horizontal, 0.0, 0.0, w, h, "#111", None, "none", 0.0, r, Rounded::Positive, false, None, None, None).unwrap(),
                )
            };
            assert_eq!(arc_radius(&drawn(true, 50.0, 4.0, 2.0)), 1.0);
            assert_eq!(arc_radius(&drawn(false, 4.0, 50.0, 2.0)), 1.0);
            assert_eq!(arc_radius(&drawn(true, 50.0, 24.0, 2.0)), 2.0);
            assert_eq!(arc_radius(&drawn(false, 24.0, 50.0, 2.0)), 2.0);
            assert_eq!(arc_radius(&drawn(true, 2.0, 24.0, 6.0)), 1.0, "length / 2 still caps a short bar");
            assert_eq!(arc_radius(&drawn(false, 24.0, 2.0, 6.0)), 1.0, "length / 2 still caps a short column");
            for svg in [drawn(true, 50.0, 4.0, 0.0), drawn(false, 4.0, 50.0, 0.0)] {
                assert!(svg.starts_with("<rect") && !svg.contains("<path"), "{svg}");
            }
        }

        /// The `<a>` opens inside the mark group and closes BEFORE the
        /// stroke-only antialiasing overlay, which is never a second linked
        /// mark — in the square and rounded shape of both orientations.
        #[test]
        fn a_stroked_datum_links_only_its_first_drawn_element() {
            for radius in [0.0, 3.0] {
                let anchor = || Some(crate::href::Anchor { url: "/x", open: crate::href::LinkOpen::Page, recede: None });
                let drawn = |horizontal| {
                    print::print(
                        &bar_passes(horizontal, 10.0, 10.0, 50.0, 20.0, "#111", None, "#fff", 1.0, radius, Rounded::Positive, true, None, Some("aria"), anchor())
                            .unwrap(),
                    )
                };
                let (bar, column) = (drawn(true), drawn(false));
                for out in [bar, column] {
                    let open_end = out.find('>').unwrap() + 1;
                    assert!(out[open_end..].starts_with("<a href=\"/x\">"), "{out}");
                    let (linked, overlay) = out.split_once("</a>").expect("anchor closes");
                    assert_eq!(linked.matches("<rect").count() + linked.matches("<path").count(), 1, "{out}");
                    assert!(linked.contains("fill=\"#111\"") && linked.contains("aria-label=\"aria\""), "{out}");
                    assert!(!linked.contains("fill=\"none\""), "{out}");
                    assert!(overlay.contains("fill=\"none\"") && overlay.ends_with("</g>"), "{out}");
                }
            }
        }

        /// The single-pass column (no stroke overlay) still closes the anchor
        /// before its group.
        #[test]
        fn a_single_pass_column_closes_its_anchor_before_its_group() {
            let out = print::print(
                &bar_passes(
                    false, 10.0, 10.0, 50.0, 20.0, "#111", None, "#fff", 1.0, 3.0, Rounded::Positive, false, None, Some("aria"),
                    Some(crate::href::Anchor { url: "/x", open: crate::href::LinkOpen::Page, recede: None }),
                )
                .unwrap(),
            );
            assert!(out.ends_with("</a></g>"), "{out}");
        }
    }

    // ── A resolved format's affix on the value axis and the tooltip ──────

    /// The plain value axis, as `compose_axis_format` paints it
    /// (`vl_field_maps.py:217`): the zero tick bare, every other tick wearing
    /// the affix. Oracle strings: `quantitative_tick_labels((0, 500, 1000,
    /// 1500, 2000), ResolvedFormat(spec=",.0f", prefix="US$"), None, None)`
    /// is `['0', 'US$500', 'US$1,000', 'US$1,500', 'US$2,000']`; with
    /// `suffix=" CHF"` it is `['0', '500 CHF', '1,000 CHF', '1,500 CHF',
    /// '2,000 CHF']`.
    #[test]
    fn a_plain_value_axis_paints_its_affix_and_leaves_the_zero_tick_bare() {
        use serde_json::json;
        for (slot, ticks) in [
            (
                json!({"spec": ",.0f", "prefix": "US$", "raw": "integer"}),
                ["US$500", "US$1,000", "US$1,500", "US$2,000"],
            ),
            (
                json!({"spec": ",.0f", "suffix": " CHF", "raw": "integer"}),
                ["500 CHF", "1,000 CHF", "1,500 CHF", "2,000 CHF"],
            ),
        ] {
            for horizontal in [false, true] {
                let mut j = make_vertical_payload(&[("Alpha", 1800.0), ("Beta", 1200.0)], false);
                if horizontal {
                    j["resolved"]["orientation"] = json!("horizontal");
                }
                j["resolved"]["style"]["axis_y"]["tick_label"] = json!(null);
                j["resolved"]["style"]["axis_y"]["labels"]["format"] = slot.clone();
                let svg = render(&make_payload(j), &test_fonts()).expect("renders");
                for tick in ticks {
                    assert!(svg.contains(&format!(">{tick}<")), "horizontal={horizontal} {tick}: {svg}");
                }
                assert!(svg.contains(">0<"), "the zero tick stays bare: {svg}");
                assert!(!svg.contains(">US$0<") && !svg.contains(">0 CHF<"), "{svg}");
            }
        }
    }

    /// The label gutter measures the affixed strings: the plot starts as far
    /// right as the widest painted label (`US$2,000`) pushes it, not the bare
    /// `2,000`.
    #[test]
    fn the_value_axis_gutter_measures_the_affixed_labels() {
        use serde_json::json;
        let plot_left_of = |slot: serde_json::Value| {
            let mut j = make_vertical_payload(&[("Alpha", 1800.0), ("Beta", 1200.0)], false);
            j["resolved"]["style"]["axis_y"]["tick_label"] = json!(null);
            j["resolved"]["style"]["axis_y"]["labels"]["format"] = slot;
            plot_left(&render(&make_payload(j), &test_fonts()).expect("renders"))
        };
        let bare = plot_left_of(json!({"spec": ",.0f", "raw": "integer"}));
        let affixed = plot_left_of(json!({"spec": ",.0f", "prefix": "US$", "raw": "integer"}));
        let fonts = test_fonts();
        let family = measure::stand_in_family(TEST_FONT);
        let widened = fonts.measure("US$2,000", family, 11.0).unwrap() - fonts.measure("2,000", family, 11.0).unwrap();
        assert!(((affixed - bare) - widened).abs() <= 1.0, "gutter grew {} not {widened}", affixed - bare);
    }

    /// Oracle: `format_d3(1800, ",.0f", "US$", "", None, placement)` is
    /// `'US$1,800'`; with `suffix=" CHF"` it is `'1,800 CHF'`.
    #[test]
    fn a_tooltip_row_paints_its_affix() {
        use serde_json::json;
        for (slot, shown) in [
            (json!({"spec": ",.0f", "prefix": "US$", "raw": "integer"}), "US$1,800"),
            (json!({"spec": ",.0f", "suffix": " CHF", "raw": "integer"}), "1,800 CHF"),
        ] {
            let mut j = make_vertical_payload(&[("Alpha", 1800.0)], false);
            j["resolved"]["style"]["tooltip_format"] = slot;
            let svg = render(&make_payload(j), &test_fonts()).expect("renders");
            assert!(svg.contains(&format!("aria-label=\"\u{2061}Alpha; Revenue: {shown}\"")), "{svg}");
        }
    }

    /// A value axis wears its affix on every non-zero tick whether the format
    /// is plain (`compose_axis_format`, `repeat: every` included) or resolve
    /// bakes a `tick_label` ladder around it (`anchor_at_start: None`), and
    /// leaves the zero tick bare. `compose_axis_format` yields to an authored
    /// `labels.expr` (`vl_field_maps.py:228`), which this arm does not
    /// evaluate, so the affix beside one refuses by name.
    #[test]
    fn a_value_axis_wears_its_affix_and_yields_to_an_authored_labels_expr() {
        use serde_json::json;
        let render_axis_y = |patch: &dyn Fn(&mut serde_json::Value)| {
            let mut j = make_vertical_payload(&[("Alpha", 1800.0)], false);
            patch(&mut j["resolved"]["style"]["axis_y"]);
            render(&make_payload(j), &test_fonts())
        };
        for (shape, patch) in [
            ("repeat: every", json!({"tick_label": null, "labels": {"format": {"spec": ",.0f", "prefix": "US$", "repeat": "every"}}})),
            ("repeat: anchor", json!({"tick_label": null, "labels": {"format": {"spec": ",.0f", "prefix": "US$", "repeat": "anchor"}}})),
            ("SI tick_label", json!({"tick_label": {"format": ".3~s", "si_format": ".3~s", "scientific_format": ".2~e"}, "labels": {"format": {"spec": ",.0f", "prefix": "US$"}}})),
            ("unbaked ladder", json!({"tick_label": {"format": ",.0f", "anchor_at_start": false}, "tick_values": [], "labels": {"format": {"spec": ",.0f", "prefix": "US$"}}})),
        ] {
            let svg = render_axis_y(&|axis| {
                for (k, v) in patch.as_object().unwrap() {
                    if k == "labels" {
                        axis["labels"]["format"] = v["format"].clone();
                    } else {
                        axis[k] = v.clone();
                    }
                }
            })
            .unwrap_or_else(|e| panic!("{shape}: {e}"));
            assert!(svg.contains(">US$500</text>") || svg.contains(">US$1.5K</text>") || svg.contains("US$"), "{shape}: {svg}");
        }
        let svg = render_axis_y(&|axis| {
            axis["labels"]["format"] = json!({"spec": ",.0f", "suffix": " CHF"});
        })
        .expect("a tick_label ladder paints its suffix");
        assert!(svg.contains(" CHF</text>") && svg.contains(">0</text>"), "{svg}");
        // `compose_axis_format` yields to an authored `labels.expr`
        // (`vl_field_maps.py:228`): the affix is dropped, only the digits stay `datum.label`.
        let mut j = make_vertical_payload(&[("Alpha", 1800.0)], false);
        j["resolved"]["style"]["axis_y"]["tick_label"] = json!(null);
        j["resolved"]["style"]["axis_y"]["labels"]["expr"] = json!("datum.label + '!'");
        j["resolved"]["style"]["axis_y"]["labels"]["format"] = json!({"spec": ",.0f", "prefix": "US$"});
        let svg = render(&make_payload(j), &test_fonts()).expect("an authored expr beside an affix renders");
        assert!(svg.contains(">1,500!</text>") && !svg.contains("US$"), "{svg}");
    }

    mod annotations {
        use super::*;
        use serde_json::{json, Value};

        const CONTRACT: &str = include_str!("../../../fixtures/annotations/contract.json");

        /// Contract entry `index`, re-anchored onto the payload under test.
        fn annotation(index: usize, x: Value, y: Value) -> Value {
            let all: Vec<Value> = serde_json::from_str(CONTRACT).unwrap();
            let mut a = all.into_iter().find(|a| a["index"] == index).unwrap();
            a["x"] = x;
            a["y"] = y;
            a
        }

        fn value(v: Value) -> Value {
            json!({"kind": "value", "value": v})
        }

        fn category(name: &str) -> Value {
            json!({"kind": "category", "value": name})
        }

        fn annotated(mut payload: Value, annotations: Vec<Value>) -> Result<String, crate::diagnostic::Diagnostic> {
            payload["resolved"]["annotations"] = Value::Array(annotations);
            render(&make_payload(payload), &test_fonts())
        }

        fn rendered(payload: Value, annotations: Vec<Value>) -> String {
            annotated(payload, annotations).expect("renders")
        }

        fn at(svg: &str, needle: &str) -> usize {
            svg.find(needle).unwrap_or_else(|| panic!("{needle} missing: {svg}"))
        }

        const BAR_FILL: &str = "fill=\"#3164a3\"";

        /// Background before the bars, Foreground after them and before either axis.
        fn assert_plane_order(svg: &str) {
            let background = at(svg, "annotations-background");
            let foreground = at(svg, "annotations-foreground");
            let bar = at(svg, BAR_FILL);
            let axis = at(svg, "data-dct-role=\"x-axis\"").min(at(svg, "data-dct-role=\"y-axis\""));
            assert!(background < bar && bar < foreground && foreground < axis, "{svg}");
        }

        /// The first `<rect>` of the Background group: `(x, y, width, height)`.
        fn background_rect(svg: &str) -> (f64, f64, f64, f64) {
            let group = &svg[at(svg, "annotations-background")..];
            let rect = &group[at(group, "<rect")..];
            let attr = |name: &str| -> f64 {
                rect.split(&format!(" {name}=\"")).nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
            };
            (attr("x"), attr("y"), attr("width"), attr("height"))
        }

        fn clip_size(svg: &str) -> (f64, f64) {
            let clip = &svg[at(svg, "<clipPath")..];
            let attr = |name: &str| -> f64 {
                clip.split(&format!(" {name}=\"")).nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
            };
            (attr("width"), attr("height"))
        }

        fn plot_top(svg: &str) -> f64 {
            let g = svg.split("<g transform=\"translate(").nth(1).unwrap();
            g.split(')').next().unwrap().split(',').nth(1).unwrap().parse().unwrap()
        }

        fn vertical() -> Value {
            make_vertical_payload(&[("East", 800.0), ("West", 600.0), ("North", 700.0)], false)
        }

        fn rule_and_band() -> Vec<Value> {
            vec![annotation(5, category("East"), Value::Null), annotation(0, Value::Null, value(json!(700.0)))]
        }

        #[test]
        fn a_bar_stack_runs_tint_rule_bars_text() {
            let mut rule = annotation(0, Value::Null, value(json!(700.0)));
            rule["plane"] = "background".into();
            let svg = rendered(vertical(), vec![annotation(5, category("East"), Value::Null), rule]);
            let group = at(&svg, "annotations-background");
            let tint = group + svg[group..].find("<rect").expect("a tint");
            let line = group + svg[group..].find("<line").expect("a rule");
            assert!(tint < line && line < at(&svg, BAR_FILL) && at(&svg, BAR_FILL) < at(&svg, "annotations-text\"><g data"), "{svg}");
        }

        #[test]
        fn a_note_on_a_horizontal_bar_slides_off_it_and_needs_no_halo() {
            let mut note = annotation(10, Value::Null, Value::Null);
            note["panel"] = Value::Null;
            note["style"]["text"]["halo_over_marks"] = true.into();
            note["style"]["text"]["position"] = json!({"edge": "bottom", "align": "start", "overlay": true, "auto_edge": false, "auto_align": false});
            // Up from under the last bar onto it.
            note["style"]["text"]["dy"] = json!(-30.0);
            let svg = rendered(make_horizontal_payload(), vec![note]);
            // Beta's bar ends at 320.4: the text starts 8px past it, on open ground.
            assert!(svg.contains("<text x=\"328.4\""), "{svg}");
            assert!(!svg.contains("stroke-linejoin=\"round\">Dashed = forecast"), "{svg}");
        }

        #[test]
        fn a_vertical_bar_paints_annotations_behind_and_over_the_bars() {
            let svg = rendered(vertical(), rule_and_band());
            assert_plane_order(&svg);
            assert!(svg.contains("<clipPath id=\"bar-ann0\">") || svg.contains("-ann0\">"), "{svg}");
        }

        #[test]
        fn a_horizontal_bar_paints_annotations_behind_and_over_the_bars() {
            let svg = rendered(make_horizontal_payload(), vec![
                annotation(5, category("Alpha"), Value::Null),
                annotation(0, Value::Null, value(json!(700.0))),
            ]);
            assert_plane_order(&svg);
        }

        #[test]
        fn a_continuous_x_bar_paints_annotations_behind_and_over_the_bars() {
            let xs: Vec<(f64, f64)> = (0..24).map(|h| (h as f64, 100.0 + h as f64)).collect();
            let svg = rendered(make_continuous_payload(&xs), vec![
                annotation(6, json!({"kind": "range", "from": 4, "to": 9}), Value::Null),
                annotation(0, Value::Null, value(json!(110.0))),
            ]);
            assert_plane_order(&svg);
        }

        #[test]
        fn no_annotations_paint_nothing() {
            let xs: Vec<(f64, f64)> = (0..24).map(|h| (h as f64, 100.0 + h as f64)).collect();
            for payload in [vertical(), make_horizontal_payload(), make_continuous_payload(&xs)] {
                let svg = rendered(payload, Vec::new());
                assert!(!svg.contains("annotations") && !svg.contains("-ann0"), "{svg}");
            }
        }

        #[test]
        fn rule_text_above_the_top_tick_reserves_top_margin() {
            let plain = rendered(vertical(), Vec::new());
            let ruled = rendered(vertical(), vec![annotation(0, Value::Null, value(json!(2000.0)))]);
            assert!(plot_top(&ruled) > plot_top(&plain) + 5.0, "{} -> {}", plot_top(&plain), plot_top(&ruled));
        }

        #[test]
        fn the_endpoint_rail_concat_refuses_text_past_the_plot() {
            let mut payload = make_stacked_payload("vertical", "zero");
            payload["resolved"]["style"]["axis_y"]["tick_values"] = json!([]);
            payload["resolved"]["style"]["axis_y"]["ticks"]["count"] = json!(null);
            payload["resolved"]["stacked_domain_max"] = json!(100.44);
            payload["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
            payload["resolved"]["style"]["series_label"]["gap_px"] = json!(8.0);
            payload["height"] = json!(600.0);
            let err = annotated(payload, vec![annotation(0, Value::Null, value(json!(110.0)))]).unwrap_err();
            assert_eq!(err.code, "ERR-ANNOTATION-MARGIN-UNSUPPORTED");
        }

        /// The rail arm re-nices the value domain (100.44 -> 110); rules map on
        /// the niced scale the stacks draw with, so 55 sits at half height.
        #[test]
        fn the_endpoint_rail_concat_maps_rules_on_its_niced_domain() {
            let mut payload = make_stacked_payload("vertical", "zero");
            payload["resolved"]["style"]["axis_y"]["tick_values"] = json!([]);
            payload["resolved"]["style"]["axis_y"]["ticks"]["count"] = json!(null);
            payload["resolved"]["stacked_domain_max"] = json!(100.44);
            payload["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
            payload["resolved"]["style"]["series_label"]["gap_px"] = json!(8.0);
            payload["height"] = json!(600.0);
            let rule = |v: f64| {
                let mut a = annotation(0, Value::Null, value(json!(v)));
                a["text"] = Value::Null;
                a
            };
            let svg = rendered(payload, vec![rule(0.0), rule(55.0)]);
            let group = &svg[at(&svg, "annotations-foreground")..];
            let ys: Vec<f64> = group
                .split("<line")
                .skip(1)
                .take(2)
                .map(|l| l.split(" y1=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap())
                .collect();
            let (_, plot_h) = clip_size(&svg);
            assert!(((ys[0] - ys[1]) - plot_h / 2.0).abs() < 0.5, "{ys:?} vs plot height {plot_h}: {svg}");
        }

        /// The stacked rail payload on a dated category axis, with a labeled
        /// trend over it whose text sits outside the line end.
        fn dated_rail_with_trend(position: Value) -> Result<String, crate::diagnostic::Diagnostic> {
            let mut payload = make_stacked_payload("vertical", "zero");
            payload["resolved"]["style"]["axis_y"]["tick_values"] = json!([]);
            payload["resolved"]["style"]["axis_y"]["ticks"]["count"] = json!(null);
            payload["resolved"]["stacked_domain_max"] = json!(100.44);
            payload["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
            payload["resolved"]["style"]["series_label"]["gap_px"] = json!(8.0);
            payload["height"] = json!(600.0);
            for row in payload["rows"].as_array_mut().unwrap() {
                row["cat"] = json!(if row["cat"] == "A" { "2024-01-01" } else { "2024-02-01" });
            }
            let mut trend = annotation(12, Value::Null, Value::Null);
            trend["text"] = json!("total trend, wider than the last band");
            trend["style"]["text"]["max_width"] = Value::Null;
            trend["trend"] = json!({"line": [["2024-01-01", 40.0], ["2024-02-01", 25.0]], "interval": null});
            trend["style"]["text"]["position"] = position;
            annotated(payload, vec![trend])
        }

        /// A dated bar chart with a right legend and a long labeled trend outside the plot.
        fn legend_beside_trend(orientation: &str, stack: &str) -> String {
            let mut payload = make_stacked_payload(orientation, stack);
            payload["resolved"]["legend"]["visible"] = json!(true);
            payload["resolved"]["legend"]["position"] = json!({"edge": "right", "align": "start", "overlay": false});
            payload["resolved"]["legend"]["direction"] = json!("vertical");
            for row in payload["rows"].as_array_mut().unwrap() {
                row["cat"] = json!(if row["cat"] == "A" { "2024-01-01" } else { "2024-02-01" });
            }
            let mut trend = annotation(12, Value::Null, Value::Null);
            trend["text"] = json!("a trend label wider than the gap");
            trend["style"]["text"]["max_width"] = Value::Null;
            trend["trend"] = json!({"line": [["2024-01-01", 40.0], ["2024-02-01", 25.0]], "interval": null});
            trend["style"]["text"]["position"] = json!({"edge": "right", "align": "center", "overlay": false, "auto_edge": false, "auto_align": false});
            let svg = rendered(payload, vec![trend]);
            let text = &svg[at(&svg, ">a trend label wider than the gap</text>")..];
            let tag = &svg[..svg.len() - text.len()];
            let text_x: f64 = tag[tag.rfind("<text").unwrap()..].split("x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
            let label = svg.split("role-legend-label").nth(1).unwrap_or_else(|| panic!("no legend: {svg}"));
            let legend_x: f64 = label.split("<text x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
            format!("{}", legend_x - text_x)
        }

        #[test]
        fn a_side_legend_clears_trend_text_outside_the_plot_on_every_bar_arm() {
            for (orientation, stack) in [("vertical", "none"), ("vertical", "zero"), ("horizontal", "none"), ("horizontal", "zero")] {
                let gap: f64 = legend_beside_trend(orientation, stack).parse().unwrap();
                // 32 characters at 11px span about 170px.
                assert!(gap > 170.0, "{orientation} {stack}: legend only {gap} past the trend text");
            }
        }

        #[test]
        fn a_stacked_rail_trend_label_outside_the_line_end_reserves_the_right_margin() {
            let outside = json!({"edge": "right", "align": "center", "overlay": false, "auto_edge": false, "auto_align": false});
            let svg = dated_rail_with_trend(outside).expect("trend text beside the rail arm renders");
            assert!(svg.contains(">total trend, wider than the last band</text>"), "{svg}");
        }

        #[test]
        fn the_horizontal_top_rail_refuses_text_past_the_plot() {
            let mut payload = make_stacked_payload("horizontal", "zero");
            payload["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "height": 20.0, "label_offset": 6.0});
            payload["resolved"]["style"]["series_label"] = json!({
                "font_family": TEST_FONT, "font_size": 11.0, "font_weight": "400",
                "font_style": "normal", "dark_companion_palette": ["#111111", "#222222"], "pin_companions": []
            });
            let err = annotated(payload, vec![annotation(0, Value::Null, value(json!(50.0)))]).unwrap_err();
            assert_eq!(err.code, "ERR-ANNOTATION-MARGIN-UNSUPPORTED");
        }

        #[test]
        fn a_time_axis_bar_refuses_annotations_by_name() {
            let mut payload = make_vertical_payload(
                &[("2025-01-01", 500.0), ("2025-01-02", 1000.0), ("2026-01-01", 1500.0)],
                false,
            );
            payload["resolved"]["style"]["mark"]["border"]["radius"] = json!(0.0);
            payload["resolved"]["style"]["label_usable_ratio"] = json!(0.8);
            payload["resolved"]["style"]["axis_x"]["fill"] = json!("null");
            let err = annotated(payload, vec![annotation(0, Value::Null, value(json!(700.0)))]).unwrap_err();
            assert_eq!(err.code, "ERR-ANNOTATION-UNSUPPORTED");
            assert_eq!(err.params["chart_type"], "time-axis bar");
        }

        /// A layout that fails has no `Layout` to carry its findings: the WARNs it collected before the
        /// refusal reach the sink, as they did when the render wrote to it directly.
        #[test]
        fn a_failed_layout_forwards_the_warnings_it_collected() {
            let mut payload = make_vertical_payload(
                &[("2025-01-01", 500.0), ("2025-01-02", 1000.0), ("2026-01-01", 1500.0)],
                false,
            );
            payload["resolved"]["style"]["mark"]["border"]["radius"] = json!(0.0);
            payload["resolved"]["style"]["label_usable_ratio"] = json!(0.8);
            payload["resolved"]["style"]["axis_x"]["fill"] = json!("null");
            payload["resolved"]["title"] = json!("T ".repeat(300));
            let (rendered, sunk) = crate::diagnostic::collect(|| {
                annotated(payload, vec![annotation(0, Value::Null, value(json!(700.0)))])
            });
            assert_eq!(rendered.unwrap_err().code, "ERR-ANNOTATION-UNSUPPORTED");
            let codes: Vec<_> = sunk.iter().map(|d| d.code).collect();
            assert_eq!(codes, ["WARN-CHART-TITLE-TRUNCATED"]);
        }

        #[test]
        fn a_number_anchor_on_a_category_axis_is_refused_by_name() {
            let range = json!({"kind": "range", "from": 4, "to": 9});
            for payload in [vertical(), make_horizontal_payload()] {
                let err = annotated(payload, vec![annotation(6, range.clone(), Value::Null)]).unwrap_err();
                assert_eq!(err.code, "ERR-ANNOTATION-UNSUPPORTED");
            }
        }

        #[test]
        fn a_date_range_band_covers_the_slots_it_names() {
            let mut payload = vertical();
            payload["rows"] = json!([
                {"product": "2024-01-01", "revenue": 800.0},
                {"product": "2024-02-01", "revenue": 600.0},
                {"product": "2024-03-01", "revenue": 700.0}
            ]);
            let range = json!({"kind": "range", "from": "2024-02-01", "to": "2024-03-01"});
            let svg = rendered(payload, vec![annotation(3, range, Value::Null)]);
            let (x, _, width, _) = background_rect(&svg);
            let second_bar: f64 = svg.split("d=\"M ").nth(2).unwrap().split(',').next().unwrap().parse().unwrap();
            let (plot_w, _) = clip_size(&svg);
            assert!(x <= second_bar && second_bar < x + width && width < plot_w / 2.0, "{x} {width} vs bar {second_bar}");
        }

        #[test]
        fn an_anchor_past_the_data_widens_the_continuous_x_extent() {
            let xs: Vec<(f64, f64)> = (0..24).map(|h| (h as f64, 100.0 + h as f64)).collect();
            let plain = bar_widths(&rendered(make_continuous_payload(&xs), Vec::new()));
            let band = annotation(6, json!({"kind": "range", "from": 30, "to": 60}), Value::Null);
            let widened = bar_widths(&rendered(make_continuous_payload(&xs), vec![band]));
            assert!(widened[0] < plain[0] - 1.0, "bars narrow as the domain grows: {} -> {}", plain[0], widened[0]);
        }

        fn facet_annotation(panel: Value) -> Value {
            let mut a = annotation(0, Value::Null, value(json!(1500.0)));
            a["panel"] = panel;
            a
        }

        #[test]
        fn an_annotation_names_the_facet_panel_it_draws_on() {
            let on_north = rendered(super::facet::make_facet_payload("shared"), vec![facet_annotation(json!("[\"North\"]"))]);
            assert_eq!(on_north.matches("annotations-foreground").count(), 1, "{on_north}");
            assert!(on_north.contains("-ann0\"") && !on_north.contains("-ann1\""), "{on_north}");
            let on_every_panel = rendered(super::facet::make_facet_payload("shared"), vec![facet_annotation(Value::Null)]);
            assert_eq!(on_every_panel.matches("annotations-foreground").count(), 2, "{on_every_panel}");
            assert!(on_every_panel.contains("-ann0\"") && on_every_panel.contains("-ann1\""), "{on_every_panel}");
        }

        #[test]
        fn a_category_band_covers_the_first_bars_slot_down_the_plot() {
            let svg = rendered(vertical(), vec![annotation(5, category("East"), Value::Null)]);
            let (x, y, width, height) = background_rect(&svg);
            let (plot_w, plot_h) = clip_size(&svg);
            let bar_left: f64 = svg.split("d=\"M ").nth(1).unwrap().split(',').next().unwrap().parse().unwrap();
            assert!(x <= bar_left && bar_left < x + width && width < plot_w / 2.0, "{x} {width} vs bar {bar_left}");
            assert_eq!((y, height), (0.0, plot_h), "a column band spans the plot height");
        }

        #[test]
        fn a_category_band_on_a_horizontal_bar_runs_across_the_plot() {
            let svg = rendered(make_horizontal_payload(), vec![annotation(5, category("Alpha"), Value::Null)]);
            let (x, y, width, height) = background_rect(&svg);
            let (plot_w, plot_h) = clip_size(&svg);
            assert_eq!((x, width), (0.0, plot_w), "a row band spans the plot width");
            assert!(y == 0.0 && height < plot_h * 0.6, "the first of two rows: y {y} height {height}");
        }
    }

    // ── The family contract ──────────────────────────────────────────────

    mod contract {
        use super::*;

        fn bound_of(json: serde_json::Value) -> (Payload, FontBook) {
            (make_payload(json), test_fonts())
        }

        /// `bind` reads no font: the folded rows and the wide series layout reads are there before any size is known.
        #[test]
        fn bind_unfolds_a_wide_chart_into_the_rows_and_series_layout_reads() {
            let (payload, _) = bound_of(make_wide_horizontal_payload());
            let bound = super::super::bind::bind(&payload).expect("bar binds");
            assert_eq!(bound.wide_series, ["profit", "revenue"]);
            assert_eq!(bound.payload.rows.len(), 4, "two rows folded over two measures");
        }

        /// A stack outside the four modes is refused at bind, ahead of any layout.
        #[test]
        fn bind_refuses_a_stack_mode_it_does_not_model() {
            let mut json = make_vertical_payload(&[("a", 1.0), ("b", 2.0)], true);
            json["resolved"]["stack"] = serde_json::json!("sideways");
            let (payload, _) = bound_of(json);
            let err = super::super::bind::bind(&payload).err().expect("bind refuses");
            assert_eq!(err.to_string(), "bar: stack=\"sideways\" is none, zero, center or normalize only");
        }

        #[test]
        fn a_bar_hugs_nothing_and_takes_the_slot_height() {
            let (payload, fonts) = bound_of(make_vertical_payload(&[("a", 1.0), ("b", 2.0)], false));
            let bound = super::super::bind::bind(&payload).expect("bar binds");
            let measure = measure::Measure { fonts: &fonts };
            assert_eq!(super::super::layout::intrinsic(&bound, &measure).unwrap(), None);
            assert_eq!(super::super::layout::height_for_width(&bound, 123.0, &measure).unwrap(), payload.height);
        }

        /// `layout` draws the cell it is given and raises its WARNs itself: the title is cut and the
        /// finding comes back in the layout, none reaching the sink.
        #[test]
        fn layout_draws_the_given_cell_and_returns_its_warnings() {
            let long = "A title that keeps going and going until no budget holds it, however it wraps";
            for orientation in ["vertical", "horizontal"] {
                let mut json = if orientation == "vertical" {
                    make_vertical_payload(&[("a", 1.0), ("b", 2.0)], false)
                } else {
                    make_horizontal_payload()
                };
                json["resolved"]["title"] = serde_json::json!(long.repeat(4));
                let (payload, fonts) = bound_of(json);
                let bound = super::super::bind::bind(&payload).expect("bar binds");
                let cell = crate::layout::Rect { x: 0.0, y: 0.0, width: 420.0, height: 260.0 };
                let (laid, sunk) = crate::diagnostic::collect(|| {
                    super::super::layout::layout(&bound, cell, &measure::Measure { fonts: &fonts })
                });
                let laid = laid.expect("bar lays out");
                assert!(sunk.is_empty(), "{orientation}: {sunk:?}");
                let codes: Vec<_> = laid.diagnostics.iter().map(|d| d.code).collect();
                assert_eq!(codes, ["WARN-CHART-TITLE-TRUNCATED"], "{orientation}");
                let [scene::Node::Svg { extent, .. }] = laid.nodes.as_slice() else { panic!("one root") };
                assert_eq!((extent.width, extent.height), (420.0, 260.0), "{orientation}");
                let data = laid.data_box;
                assert!(
                    data.x >= 0.0 && data.y >= 0.0 && data.x + data.width <= 420.0 && data.y + data.height <= 260.0,
                    "{orientation}: {data:?}"
                );
                assert_eq!(laid.insets, cell.insets_of(&data), "{orientation}");
            }
        }

        /// A `multiples` chart binds, but only `render` composes it: `layout` draws one plot and refuses.
        #[test]
        fn layout_refuses_a_multiples_chart() {
            let (payload, fonts) = bound_of(facet::make_facet_payload("shared"));
            let bound = super::super::bind::bind(&payload).expect("a multiples chart binds");
            let cell = crate::layout::Rect { x: 0.0, y: 0.0, width: payload.width, height: payload.height };
            let err = super::super::layout::layout(&bound, cell, &measure::Measure { fonts: &fonts })
                .err()
                .expect("layout refuses");
            assert_eq!(err.to_string(), "bar: layout draws one plot; a multiples chart is composed by render");
        }

        /// `render` is `bind`, `layout` into the payload's card, and `print`; the warnings reach the
        /// sink once, from the layout's own list.
        #[test]
        fn render_prints_the_layout_of_the_bound_chart() {
            let mut json = make_vertical_payload(&[("a", 1.0), ("b", 2.0)], false);
            json["resolved"]["title"] = serde_json::json!("T ".repeat(300));
            let (payload, fonts) = bound_of(json);
            let bound = super::super::bind::bind(&payload).expect("bar binds");
            let cell = crate::layout::Rect { x: 0.0, y: 0.0, width: payload.width, height: payload.height };
            let laid = super::super::layout::layout(&bound, cell, &measure::Measure { fonts: &fonts }).unwrap();
            let (svg, sunk) = crate::diagnostic::collect(|| render(&payload, &fonts));
            assert_eq!(svg.unwrap(), print::print(&laid.nodes));
            assert!(!laid.diagnostics.is_empty(), "the payload must warn for forwarding to be tested");
            assert_eq!(sunk, laid.diagnostics);
        }
    }
}
