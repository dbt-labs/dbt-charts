//! Scatter family — a quantitative x against either a quantitative y or a
//! categorical one (production's dot plot: a `point`-scale band per
//! category), OR a categorical x against a quantitative y (a `point`-scale
//! band per category on x, `scale.padding` read from the resolved axis —
//! the theme bakes `axis_x.scale.padding: 0`, unlike the dot plot's
//! hardcoded 0.5 on y, since only x's theme block carries that override),
//! with an optional nominal color channel, an optional quantitative size
//! channel (Vega's zero-anchored linear size scale on the marks, and a second
//! symbol legend stacked under the color legend) and an optional nominal shape
//! channel. x and y are never
//! BOTH categorical — the fit shape always keeps one continuous axis. A
//! wide (`y: [a, b]`) chart unfolds into long rows once, in
//! `bind`, before any other law runs — same seam as bar.rs/line.rs
//! (`common::unfold_wide_rows`'s own doc comment), landing a nominal x on
//! the fit arm with one colored point per measure per category (the
//! "dot/band plot").
//!
//! Unsupported → clear Err:
//! - multiples outside the date-x, measure-y, right-axis shape `render_facet`
//!   models (each other shape refuses by name)
//! - a shape channel with wide measures
//! - a size channel with a non-numeric value
//! - a mixed numeric/categorical y column
//! - a nominal x channel together with a nominal y channel (no continuous
//!   axis left to fit against)
//! - a non-numeric `sort.by`

use crate::diagnostic::Diagnostic;
use crate::time_axis::{Grain as AxisGrain, TimeAxis};
use std::collections::HashMap;
use std::collections::HashSet;

use serde::Deserialize;
use serde_json::Value;

use crate::common::color_domain_of;
use crate::common::{
    format_y_label, row_num, row_str, symbol_radius, AxisStyle, ChartSort, Font,
    FormatSlot, RulerConfig, StyleChannel, TickLabelConfig, TitleStyle,
};
use crate::families::axis_caption::{vega_axis_format, AxisCaption, CaptionScale, NumericScale};
use crate::families::layers::{
    claim_layers, panel_layer_rows, resolve_layer_rows,
    OverlayBarLayer, OverlayLayer,
    OverlayLineLayer, OverlayScatterLayer, YSides,
};
use crate::families::facet_compose;
use crate::families::temporal::{infer_vega_type, VegaType};
use crate::families::color_scale::{ContinuousColor, GradientAt};
use crate::families::legend;
use crate::families::facet_title::{layout_title, TitleLayout, TitleSource};
use crate::families::multiples;
use print::print;
use measure::{FontBook, Measure};
use crate::vega_fit;
use crate::diagnostic::warn;
use crate::layout::Rect;
use crate::Payload;
use scene::{Attrs, Node};

pub mod bind;
pub mod layout;

/// One rendered point: (x fraction, y fraction, color series, radius, own
/// row -- the row backs its structured-tooltip aria-label). Both fractions
/// are already resolved to [0, 1] of the plot box — continuous or nominal,
/// x and y each collapse to the same one representation before a point is
/// built, so nothing downstream re-derives a scale.
type PointDatum<'a> = (f64, f64, String, f64, &'a serde_json::Map<String, Value>);

/// The x scale's ticks and per-kept-row points, as fractions of a plot
/// `pw` wide.
struct XFrame {
    ticks: Vec<f64>,
    labels: Vec<String>,
    /// The scale Vega's axis description reads; a temporal axis owns its own caption.
    scale: Option<CaptionScale>,
    points: Vec<f64>,
    /// The x threshold rules (`datum: 0`, `datum: 1`) that fired, where they land.
    rules: [Option<f64>; 2],
}

/// A scale position that is `NaN` draws at the range's origin (Vega's SVG
/// renderer prints `x || 0`).
fn finite_frac(frac: f64) -> f64 {
    if frac.is_nan() { 0.0 } else { frac }
}

/// A scatter's base y scale: a continuous bend over its domain, or a category
/// y's point scale (each category's fraction of the plot from the top).
enum YScale {
    Bent(vega_fit::Bend, (f64, f64)),
    Points { fracs: Vec<f64>, domain: Vec<String> },
}

/// A quantitative x scale of linear, pow, sqrt or symlog type. Vega joins the
/// raw domain to zero when `zero`, pads it by `pad` pixels (`padDomain`, in
/// the transformed space), rounds the result out when `nice`, and ticks it
/// linearly.
#[derive(Clone)]
pub(crate) struct LinearX {
    /// The authored pair verbatim, else the data extent.
    raw: (f64, f64),
    zero: bool,
    nice: bool,
    /// The domain is the data extent itself (no authored pair, zero not joined):
    /// no point may sit on a non-zero edge.
    fit: bool,
    pad: f64,
    bend: vega_fit::Bend,
    tick_count: usize,
    /// The axis's `tickMinStep`, capping `tick_count` at the domain's span.
    min_step: Option<f64>,
    /// The authored label format; unset, d3's default tick format.
    format: Option<FormatSlot>,
    /// The baked ruler / tick-label ladder `inject_axis_numeral_expr` paints the format through.
    ruler: Option<RulerConfig>,
    tick_label: Option<TickLabelConfig>,
    /// The authored `labels.expr`, spoken over each tick's digits.
    expr: Option<String>,
    case: Option<String>,
    /// The `datum` of each threshold rule that fires.
    rule_datums: [Option<f64>; 2],
}

/// `to`, unless moving from `from` would cross zero: a zero edge is fine, a past-zero one is not.
fn clamp_to_zero(from: f64, to: f64) -> f64 {
    if from.signum() == to.signum() { to } else { 0.0 }
}

impl LinearX {
    /// The scale's domain on a plot `pw` wide.
    pub(crate) fn domain_at(&self, pw: f64) -> (f64, f64) {
        let (lo, hi) = self.raw;
        let (lo, hi) = if self.zero { (lo.min(0.0), hi.max(0.0)) } else { (lo, hi) };
        let (lo, hi) = vega_fit::pad_domain((lo, hi), pw, self.pad, self.bend);
        if !self.nice || lo.is_nan() || hi.is_nan() {
            (lo, hi)
        } else if lo > hi {
            let (nice_hi, nice_lo) = d3rs::ticks::d3_nice(hi, lo, 10);
            (nice_lo, nice_hi)
        } else if self.fit {
            self.nice_clear_of_data(lo, hi)
        } else {
            d3rs::ticks::d3_nice(lo, hi, 10)
        }
    }

    /// A data-fit domain rounded out to the axis's own tick boundaries, then
    /// one tick step further past a non-zero end a data value still lands on:
    /// a dot on the plot edge reads as clipped, unless the edge is zero.
    fn nice_clear_of_data(&self, lo: f64, hi: f64) -> (f64, f64) {
        let (nice_lo, nice_hi) = d3rs::ticks::d3_nice(lo, hi, self.tick_count);
        if nice_hi <= nice_lo {
            return (nice_lo, nice_hi);
        }
        let count = d3rs::ticks::vega_tick_count(nice_lo, nice_hi, self.tick_count, self.min_step);
        let step = d3rs::ticks::d3_tick_step(nice_lo, nice_hi, count);
        let (data_lo, data_hi) = self.raw;
        // An authored tick step can exceed the step the domain rounded to, so clamp at zero.
        (
            if data_lo == nice_lo && nice_lo != 0.0 { clamp_to_zero(nice_lo, nice_lo - step) } else { nice_lo },
            if data_hi == nice_hi && nice_hi != 0.0 { clamp_to_zero(nice_hi, nice_hi + step) } else { nice_hi },
        )
    }

    /// A position as a fraction of the plot. d3 puts every value of a
    /// collapsed domain at the midpoint; a `NaN` scale (an unparsable
    /// authored domain) draws every mark at the plot's origin.
    pub(crate) fn frac(&self, domain: (f64, f64), v: f64) -> f64 {
        finite_frac(self.bend.frac(domain, v))
    }

    /// Ticks, labels and rules at plot width `pw`.
    pub(crate) fn at(&self, pw: f64) -> Result<(Vec<f64>, Vec<String>, [Option<f64>; 2], (f64, f64)), String> {
        let domain = self.domain_at(pw);
        if domain.0.is_nan() || domain.1.is_nan() {
            return Ok((Vec::new(), Vec::new(), self.rule_datums.map(|_| None), domain));
        }
        let count = d3rs::ticks::vega_tick_count(domain.0, domain.1, self.tick_count, self.min_step);
        let values = d3rs::ticks::d3_ticks(domain.0, domain.1, count);
        let labels: Vec<String> = match &self.format {
            Some(slot) => values
                .iter()
                .enumerate()
                .map(|(i, &tv)| {
                    format_y_label(tv, self.ruler.as_ref(), self.tick_label.as_ref(), Some(slot), i, values.len())
                        .map_err(|e| format!("scatter x label format: {e}"))
                })
                .collect::<Result<_, _>>()?,
            // d3's `tickFormat` default: comma-grouped fixed point at the
            // precision the tick step implies.
            None => {
                let step = d3rs::ticks::d3_tick_step(domain.0, domain.1, count);
                let spec = crate::number_locale::vega_tick_spec(step);
                values
                    .iter()
                    .map(|&tv| {
                        crate::number_locale::d3_format(&spec, tv).map_err(|e| format!("scatter x label format: {e}"))
                    })
                    .collect::<Result<_, _>>()?
            }
        };
        let labels = match self.expr.as_deref() {
            // The expression speaks over the format's digits alone: the affix is dropped.
            Some(src) => labels
                .iter()
                .zip(&values)
                .enumerate()
                .map(|(i, (label, &tv))| {
                    let digits = match &self.format {
                        Some(slot) => crate::number_locale::d3_format(&slot.spec, tv).map_err(|e| format!("scatter x label format: {e}"))?,
                        None => label.clone(),
                    };
                    crate::labels_expr::speak_value("x", src, tv, &digits, i, values.len())
                })
                .collect::<Result<Vec<String>, String>>()?,
            None => labels,
        };
        let labels = labels.iter().map(|label| crate::common::label_case(self.case.as_deref(), label)).collect::<Result<Vec<_>, _>>()?;
        let ticks = values.iter().map(|&tv| self.frac(domain, tv)).collect();
        Ok((ticks, labels, self.rule_datums.map(|d| d.map(|datum| self.frac(domain, datum))), domain))
    }
}

#[derive(Deserialize)]
struct ScatterResolved {
    /// The chart's id: namespaces the gradient a quantitative color mints.
    id: String,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    title: Option<String>,
    title_style: Option<TitleStyle>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    subtitle: Option<String>,
    /// Effective chart background — see `BarResolved::background` and
    /// `docs/laws-shared.md#background`.
    background: String,
    x: String,
    y: String,
    #[serde(default)]
    x_label: Option<String>,
    #[serde(default)]
    y_label: Option<String>,
    field_labels: crate::common::FieldLabels,
    #[serde(default)]
    tooltip: Vec<crate::families::tooltip_extra::TooltipField>,
    /// The format a quantitative x's tooltip row names for itself; absent, x
    /// keeps the measure format's digits without its affix.
    #[serde(default)]
    x_tooltip_format: Option<FormatSlot>,
    color: Option<String>,
    /// The color column's own format: unmodeled on a quantitative color, whose
    /// legend labels take Vega's default.
    #[serde(default)]
    color_format: Option<FormatSlot>,
    palette: Vec<String>,
    /// The palette a series color reads: the chart's own, else the board's, else Vega's default.
    #[serde(skip)]
    series_palette: Vec<String>,
    /// Board-wide value→slot bindings for this chart's categorical fields
    /// (#7167) — see `common::category_scale_for`/`series_color`.
    #[serde(default)]
    category_colors: Vec<crate::common::CategoryColorScale>,
    /// Orders a categorical channel's band domain; inert on a measure.
    #[serde(default)]
    sort: Option<ChartSort>,
    multiples: Option<crate::families::multiples::MultiplesConfig>,
    /// Baked small-multiples partition dimensions (`resolved.panel_axes`).
    #[serde(default)]
    panel_axes: Vec<crate::families::multiples::PartitionAxis>,
    #[serde(default)]
    layers: Vec<Value>,
    #[serde(default)]
    annotations: Vec<crate::annotation::Annotation>,
    /// The query the chart reads; a layer reading another one draws its own rows.
    query_name: Option<String>,
    /// The field name a size channel binds — a quantitative-only encoding
    /// (`emitters/scatter.py`'s `field_encoding(chart.size, "quantitative")`,
    /// unconditional), so it's read as a field name, never a literal value.
    size: Option<String>,
    /// The field name a shape channel binds — a nominal-only encoding.
    shape: Option<String>,
    /// A `y: [a, b]` fold — see the wide-measure fold block at the top of
    /// `render`. Empty on a plain (single-`y`) scatter.
    #[serde(default)]
    wide_measures: Vec<String>,
    /// Chart-root `ResolvedChart.link`: a channel-placeholder template painted
    /// as an `<a>` around each datum's marks.
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    link: Option<String>,
    #[serde(default)]
    link_open: crate::href::LinkOpen,
    /// The host's board URL scheme, from the payload; rewrites each datum href.
    #[serde(skip)]
    link_context: Option<crate::href::LinkContext>,
    /// The column a link's `{{ color }}` reads: the authored color, or on a
    /// wide chart its authored dimension, else the fold's raw measure name.
    /// Set before the fold overwrites `color`.
    #[serde(skip)]
    link_color: Option<String>,
    /// The column a link's `{{ y }}` reads: the first authored measure on a
    /// wide chart.
    #[serde(skip)]
    link_y: String,
    /// The x holds labeled buckets (`2024-Q1`), which production rewrites to real dates.
    #[serde(skip)]
    link_x_labeled: bool,
    #[serde(default)]
    resolved_channels: HashMap<String, StyleChannel>,
    legend: legend::LegendConfig,
    style: ScatterStyle,
}

#[derive(Deserialize)]
struct ScatterStyle {
    axis_x: AxisStyle,
    axis_y: AxisStyle,
    point_mark: PointMark,
    /// Width-resolved title font (production's `chart_title_spec` output) —
    /// always preferred over the un-tiered `title_style.font`.
    #[serde(default)]
    title_font: Option<Font>,
    /// Fill for a scatter with no color channel — production's
    /// `emit_scatter_layer` paints this, never `palette[0]` (the two
    /// coincide on neon/vivid, differ on clarity). Required: a payload
    /// lacking it refuses rather than falling back to the palette.
    single_series_fill: String,
    /// The format of the tooltip's y/x value rows -- an absent or empty spec
    /// means no tooltip aria-labels for this chart (`tooltip_labels` gate,
    /// same law as bar/pie/heatmap).
    #[serde(default)]
    tooltip_format: FormatSlot,
    /// Theme constant behind `vega_fit::category_label_layout`'s crowding
    /// test (bar's own `r.style.label_usable_ratio`, shared across
    /// cartesian families) — feeds the nominal-x tilt ladder below.
    #[serde(default)]
    label_usable_ratio: Option<f64>,
    /// Whether the point value-label format is an SI-shaped house alias
    /// (`common::format_mark_label`).
    #[serde(default)]
    label_is_house: bool,
}

#[derive(Deserialize)]
struct PointMark {
    size: f64,
    opacity: Option<f64>,
    stroke_width: Option<f64>,
    #[serde(default)]
    labels: Option<crate::common::MarkLabels>,
    /// Authored constant mark attributes the fit arm does not draw: each
    /// refuses by name in `render`.
    #[serde(default)]
    shape: Option<Value>,
    #[serde(default)]
    filled: Option<bool>,
    /// The mark's own color: the stroke of an unfilled point.
    #[serde(default)]
    color: Option<String>,
    /// The fill of an unfilled point.
    #[serde(default)]
    fill: Option<String>,
}

/// Vega-Lite's default mark color, the stroke of an unfilled point no color
/// channel or authored color paints.
const VL_DEFAULT_MARK_COLOR: &str = "#4c78a8";

impl ScatterResolved {
    /// The format a quantitative x row paints: its own `x_tooltip_format` when
    /// the resolve bakes one, else the measure's digits bare (the affix is y's
    /// own unit label and never paints x's unrelated measure).
    fn x_tooltip_row_format(&self) -> FormatSlot {
        self.x_tooltip_format
            .clone()
            .unwrap_or_else(|| FormatSlot { spec: self.style.tooltip_format.spec.clone(), ..FormatSlot::default() })
    }
}

/// `chart.layers` on a scatter base: line, area, scatter and bar layers in
/// authored order on the base's x and shared y scale (see
/// `docs/laws-layers.md`).
fn validate_scatter_layers(r: &ScatterResolved, payload: &Payload) -> Result<Vec<OverlayLayer>, String> {
    let overlays = r
        .layers
        .iter()
        .map(|raw| {
            let overlay = match raw.get("type").and_then(Value::as_str).unwrap_or("") {
                "line" | "area" => OverlayLayer::Line(Box::new(OverlayLineLayer::parse(raw, "scatter")?)),
                "scatter" => OverlayLayer::Scatter(Box::new(OverlayScatterLayer::parse(raw, "scatter")?)),
                "bar" => OverlayLayer::Bar(Box::new(OverlayBarLayer::parse(raw, "scatter")?)),
                other => return Err(format!("scatter: a {other:?} layer is none of line, area, scatter or bar")),
            };
            resolve_layer_rows(payload, overlay.query_name(), r.query_name.as_deref(), "scatter")?;
            Ok(overlay)
        })
        .collect::<Result<Vec<_>, String>>()?;
    Ok(overlays)
}

impl ScatterResolved {
    pub(crate) fn from_payload(payload: &Payload) -> Result<Self, String> {
        let mut r: Self = Deserialize::deserialize(&payload.resolved).map_err(|e| format!("scatter payload: {e}"))?;
        r.series_palette = payload.series_palette(&r.palette);
        r.link_context = payload.link_context.clone();
        r.link_y = r.y.clone();
        r.link_color = r.color.clone();
        r.link_x_labeled = crate::families::temporal::has_labeled_bucket(&payload.rows, &r.x);
        Ok(r)
    }
}

pub fn render(payload: &Payload, fonts: &FontBook) -> Result<String, Diagnostic> {
    let bound = bind::bind(payload)?;
    if bound.r.multiples.is_some() {
        return render_facet(&bound, fonts);
    }
    let measure = Measure { fonts };
    let cell = Rect { x: 0.0, y: 0.0, width: bound.payload.width, height: bound.payload.height };
    let laid_out = layout::layout(&bound, cell, &measure)?;
    laid_out.diagnostics.into_iter().for_each(warn);
    Ok(print(&laid_out.nodes))
}

/// The size scale's domain over the rows Vega keeps: zero-anchored.
fn size_scale_domain(
    r: &ScatterResolved,
    drawn: &[&serde_json::Map<String, Value>],
    (x_nominal, x_dates, y_nominal): (bool, bool, bool),
) -> Result<Option<(f64, f64)>, String> {
    if r.size.is_none() {
        return Ok(None);
    }
    let (lo, hi) = drawn
        .iter()
        .filter_map(|row| kept_reading(row, r, x_nominal, x_dates, y_nominal)?.2)
        .fold((0.0_f64, 0.0_f64), |(lo, hi), sv| (lo.min(sv), hi.max(sv)));
    Ok(Some((lo, hi)))
}

/// Vega-Lite's linear size scale over its fixed default point-mark range:
/// the drawn AREA for `v`; a zero-width domain maps everything to the middle of
/// the range, as d3's `normalize` does.
fn size_area((lo, hi): (f64, f64), v: f64) -> f64 {
    let (r_lo, r_hi) = SIZE_SCALE_RANGE;
    r_lo + crate::families::legend::unit_position(v, lo, hi) * (r_hi - r_lo)
}

/// A size channel's symbol legend (`legend::SizeLegend`), laid out once and painted
/// wherever the chart's legend stack puts it. It rides the same `chart.legend` config
/// as the color legend.
fn size_legend(
    r: &ScatterResolved,
    fonts: &FontBook,
    domain: (f64, f64),
    symbol: &str,
) -> Result<legend::SizeLegend, String> {
    let size_field = r.size.as_deref().expect("a size legend needs a size channel");
    let title = legend::legend_title_text(size_field, &r.legend, &r.field_labels)?;
    let (ticks, labels) = legend::size_legend_ticks(domain, false)?;
    let areas: Vec<f64> = ticks.iter().map(|&v| size_area(domain, v)).collect();
    let label_ws = legend::size_label_widths(&labels, &r.legend, fonts)?;
    let glyphs = areas
        .iter()
        .map(|&area| {
            let sr = symbol_radius(area);
            if symbol == "circle" {
                Ok(format!("M{sr:.3},0A{sr:.3},{sr:.3},0,1,1,-{sr:.3},0A{sr:.3},{sr:.3},0,1,1,{sr:.3},0"))
            } else {
                crate::families::scatter_shape::path(symbol, sr)
            }
        })
        .collect::<Result<Vec<_>, String>>()?;
    legend::SizeLegend::new(
        &title,
        legend::SizeRows { labels, glyphs, areas, label_ws },
        (SIZE_LEGEND_LABEL_OFFSET, r.legend.direction == "horizontal"),
        crate::families::scatter_shape::half_extents(symbol, 1.0),
        &r.legend,
        fonts,
    )
}

/// Paints the size legend at `(x, y)`. Symbol paint is `legend/encode.ts` `symbols()`: an
/// unfilled mark keeps its interior (stroked in the mark's color unless a
/// color channel makes the stroke a field ref); beside a color legend the
/// fill becomes `symbolBaseFillColor` (black) under a transparent base
/// stroke; with no color channel the mark's own static fill survives at
/// the mark's opacity.
fn size_legend_nodes(sl: &legend::SizeLegend, r: &ScatterResolved, x: f64, y: f64) -> Vec<Node> {
    let opacity = r.style.point_mark.opacity.unwrap_or(1.0);
    let paint = if r.style.point_mark.filled == Some(false) {
        let interior = r.style.point_mark.fill.as_deref().expect("an unfilled mark's interior is checked in bind");
        legend::SizePaint::Ring { interior, stroke: Some(unfilled_stroke(r)).filter(|_| r.color.is_none()), opacity }
    } else if r.color.is_some() {
        legend::SizePaint::Field
    } else {
        legend::SizePaint::Static { fill: &r.style.single_series_fill, opacity }
    };
    sl.nodes(&r.legend, paint, x, y)
}

/// How a shape legend's symbols are painted (`legend/encode.ts` `symbols()`):
/// an unfilled mark keeps its interior; a rule layer's point gives the legend
/// the rules' ink (the first fired rule's: y rules paint before x rules);
/// beside a color channel the fill is a field ref, black; else the mark's own.
fn shape_legend_fill<'a>(r: &'a ScatterResolved, fires: &RuleFires) -> crate::families::scatter_shape::SymbolFill<'a> {
    use crate::families::scatter_shape::SymbolFill;
    if r.style.point_mark.filled == Some(false) {
        return SymbolFill::Ring {
            interior: r.style.point_mark.fill.as_deref().expect("an unfilled mark's interior is checked in bind"),
            stroke: Some(unfilled_stroke(r)).filter(|_| r.color.is_none()),
        };
    }
    if fires.shape_layer(r) {
        let threshold = if fires.y_zero || fires.y_unity {
            r.style.axis_y.grid.threshold.as_ref()
        } else {
            r.style.axis_x.grid.threshold.as_ref()
        };
        return SymbolFill::Static(threshold.and_then(|t| t.color.as_deref()).unwrap_or("#8A8C8F"));
    }
    if r.color.is_some() {
        SymbolFill::Field
    } else {
        SymbolFill::Static(&r.style.single_series_fill)
    }
}

/// Whether a shape channel's glyphs ride the color legend: only a nominal
/// color on the same field. A numeric color draws a gradient, and a rule layer
/// (a baseline rule, or `axis_y.mirror`'s ghost rule) puts the shape on a
/// layer of its own, so each keeps its shape legend apart.
fn shape_merges_into_color(r: &ScatterResolved, rule_shape: bool, mirror: bool) -> bool {
    r.shape.is_some() && r.shape == r.color && !quantitative_color(r) && !rule_shape && !mirror
}

/// Each color-legend entry's glyph: the shape channel's own symbol when it
/// merges into the color legend, else the mark's authored shape, else the
/// circle.
fn color_legend_glyph_names(r: &ScatterResolved, entries: usize, merged_shape: bool) -> Vec<&str> {
    let authored = r.style.point_mark.shape.as_ref().and_then(Value::as_str).filter(|_| r.shape.is_none());
    (0..entries)
        .map(|i| if merged_shape { crate::families::scatter_shape::symbol_for(i) } else { authored.unwrap_or("circle") })
        .collect()
}

/// A shape channel's point on a baseline rule's layer: Vega-Lite gives every
/// rule a point mark carrying the shape scale's `undefined` category, painted
/// at `(x, y)` in the rule's ink.
fn rule_point(
    at: (f64, f64),
    (shape_field, aria, layer): (&str, Option<&String>, usize),
    (color, width): (&str, f64),
) -> Result<Vec<Node>, String> {
    let rr = crate::common::symbol_radius(RULE_POINT_SIZE);
    let point_aria = aria.map(|a| format!("{a}; {shape_field}: undefined"));
    let point = Node::Path(
        Attrs::new()
            .set("transform", format!("translate({:.4},{:.4})", at.0, at.1))
            .set("d", format!("M{rr:.3},0A{rr:.3},{rr:.3},0,1,1,-{rr:.3},0A{rr:.3},{rr:.3},0,1,1,{rr:.3},0"))
            .set("fill", color)
            .set("stroke-width", width.to_string())
            .set("opacity", "1")
            .extend(crate::contract::aria_attrs(point_aria.as_deref(), crate::contract::ItemRole::Point)?),
    );
    Ok(vec![if point_aria.is_some() {
        crate::contract::mark_group("symbol", &format!("layer_{layer}_layer_1_marks"), vec![point])?
    } else {
        point
    }])
}

/// One mark's glyph at radius `radius`: its shape category's symbol, else the
/// authored point shape, else the circle.
fn point_glyph(
    r: &ScatterResolved,
    row: &serde_json::Map<String, Value>,
    shape_domain: &[String],
    radius: f64,
) -> Result<String, String> {
    if let Some(shape_field) = r.shape.as_deref() {
        let category = row_str(row, shape_field);
        let i = shape_domain
            .iter()
            .position(|d| *d == category)
            .ok_or_else(|| format!("scatter: shape value {category:?} is outside the shape scale domain"))?;
        return crate::families::scatter_shape::path(crate::families::scatter_shape::symbol_for(i), radius);
    }
    match r.style.point_mark.shape.as_ref().and_then(Value::as_str) {
        Some(symbol) => crate::families::scatter_shape::path(symbol, radius),
        None => Ok(format!(
            "M{radius:.3},0A{radius:.3},{radius:.3},0,1,1,-{radius:.3},0A{radius:.3},{radius:.3},0,1,1,{radius:.3},0"
        )),
    }
}

/// The id a chart's gradient legend mints for its `<linearGradient>`.
fn legend_gradient_id(r: &ScatterResolved) -> String {
    format!("scatter_grad-{}", crate::contract::safe_svg_id(&r.id))
}

/// An unfilled mark's stroke with no color channel: the mark's own color.
fn unfilled_stroke(r: &ScatterResolved) -> &str {
    r.style.point_mark.color.as_deref().unwrap_or(VL_DEFAULT_MARK_COLOR)
}

/// The legends' shared stack: each block's offset from the stack origin, the
/// shape legend's y, and the stack's size.
struct LegendStack {
    color_off: (f64, f64),
    size_off: (f64, f64),
    shape_y: f64,
    size: (f64, f64),
}

/// A log value axis: the nice'd power-of-ten domain (d3-scale `log.js`
/// `nice`), d3's log ticks and the `tickFormat` minor-mantissa mask.
/// Vega-Lite compiles a log axis with `labelOverlap: "greedy"`
/// (`compile/axis/properties.ts:279-284`, `vega_fit::greedy_visible`) and no
/// default `tickCount` (`:304`), so `count` is the axis's own `tickCount`
/// (the x axis's baked 6) or d3's unset-count default of 10 (a log y axis
/// carries no baked ladder).
#[derive(Clone)]
struct LogAxis {
    lo: f64,
    hi: f64,
    ticks: Vec<f64>,
    mantissa_visible: Vec<bool>,
}

impl LogAxis {
    fn new(ax: &AxisStyle, data_min: f64, data_max: f64, count: usize, min_step: Option<f64>, extent: f64) -> Result<Self, String> {
        // The authored pair reaches Vega verbatim, so a descending one keeps its
        // order and inverts the axis; resolve rejects a non-positive bound. Only
        // an authored `nice` rounds it out to powers of ten. `scale.padding`
        // widens the pair or the data extent by pixels of the `extent` long
        // range before any rounding.
        let pad = ax.scale.get("padding").and_then(Value::as_f64).unwrap_or(0.0);
        let base = ax.log_base();
        let (lo, hi) = match ax.authored_continuous_domain() {
            Some((lo, hi)) => {
                let padded = vega_fit::pad_domain((lo, hi), extent, pad, vega_fit::Bend::Log);
                if ax.scale.get("nice").and_then(Value::as_bool) == Some(true) {
                    crate::families::cartesian::log_nice_pair(base, padded)
                } else {
                    padded
                }
            }
            None => {
                let (lo, hi) = vega_fit::pad_domain((data_min, data_max), extent, pad, vega_fit::Bend::Log);
                if ax.scale.get("nice").and_then(Value::as_bool) == Some(false) {
                    (lo, hi)
                } else {
                    crate::families::cartesian::log_nice_domain(base, lo, hi)
                }
            }
        };
        // d3 hands a descending domain's ticks back reversed, and labelOverlap
        // walks them in that order.
        let count = d3rs::ticks::vega_tick_count(lo, hi, count, min_step);
        let scale_ticks = crate::families::cartesian::log_ticks(base, lo.min(hi), lo.max(hi), count);
        let mut ticks = match ax.authored_scale_values() {
            Some(values) => crate::families::cartesian::ladder_within_domain(&values, lo, hi),
            None => scale_ticks.clone(),
        };
        // Vega's `noSkip` keeps every label of an axis with explicit values.
        let mut mantissa_visible = match ax.authored_scale_values() {
            Some(_) => vec![true; ticks.len()],
            None if ax.composes_numeral_expr() => vec![true; ticks.len()],
            None => crate::families::cartesian::log_tick_mantissa_visible(base, &ticks, scale_ticks.len(), count),
        };
        if hi < lo {
            ticks.reverse();
            mantissa_visible.reverse();
        }
        Ok(Self { lo, hi, ticks, mantissa_visible })
    }

    /// The value's fraction of the plot height from the top.
    fn frac_from_top(&self, v: f64) -> f64 {
        self.span_frac(self.hi.ln() - v.ln())
    }

    /// The value's fraction of the plot width from the left.
    fn frac_from_left(&self, v: f64) -> f64 {
        self.span_frac(v.ln() - self.lo.ln())
    }

    /// `offset` as a fraction of the domain's log span; d3 centers every value
    /// of a degenerate domain.
    fn span_frac(&self, offset: f64) -> f64 {
        if self.lo == self.hi {
            return 0.5;
        }
        offset / (self.hi.ln() - self.lo.ln())
    }
}

/// Which tick labels survive `labelOverlap`: a log axis runs Vega's greedy
/// strategy and every other continuous one parity. Only the mantissa
/// survivors (non-empty labels) are candidates — a blanked tick never blocks
/// its neighbors. `ink(i)` is label `i`'s bounds at the current plot size.
fn visible_tick_labels(
    labels: &[String],
    separation: f64,
    greedy: bool,
    ink: impl Fn(usize) -> vega_fit::Bounds,
) -> Vec<bool> {
    let survivors: Vec<usize> = (0..labels.len()).filter(|&i| !labels[i].is_empty()).collect();
    let bounds: Vec<vega_fit::Bounds> = survivors.iter().map(|&i| ink(i)).collect();
    let mut visible = vec![false; labels.len()];
    let shown = if greedy { vega_fit::greedy_visible(&bounds, separation) } else { vega_fit::parity_visible(&bounds, separation) };
    for (&i, v) in survivors.iter().zip(shown) {
        visible[i] = v;
    }
    visible
}

/// A log x axis over the data extent, whose padded domain waits on the plot width.
#[derive(Clone, Copy)]
struct LogX<'a> {
    ax: &'a AxisStyle,
    extent: (f64, f64),
}

impl LogX<'_> {
    fn ladder(&self, pw: f64) -> Result<LogAxis, String> {
        Ok(log_x_axis(self.ax, self.extent.0, self.extent.1, pw)?.0)
    }
}

/// Where a bar layer's rects sit on the shared x scale.
struct BarX<'a> {
    linear: Option<&'a LinearX>,
    log: Option<LogX<'a>>,
    /// A band x's bandwidth as a fraction of the plot width.
    bandwidth: Option<f64>,
    /// A rounded category scale's geometry, whose bandwidth floors with the plot width.
    nominal_round: Option<NominalRound>,
    temporal: Option<&'a crate::families::scatter_axis::TemporalXAxis>,
}

/// The d3 band (or point, `inner` 1) scale a `scale.round` category x is: its
/// padding terms and category count.
#[derive(Clone, Copy)]
struct NominalRound {
    inner: f64,
    outer: f64,
    n: usize,
}

impl NominalRound {
    /// Band starts and bandwidth in pixels on a plot `pw` wide.
    fn at(&self, pw: f64) -> (Vec<f64>, f64) {
        round_band_positions(pw, self.n, self.inner, self.outer)
    }

    /// `frac`, one of the unrounded band centers `centers`, moved to its band's rounded center.
    fn snap(&self, frac: f64, centers: &[f64], pw: f64) -> f64 {
        let (starts, bandwidth) = self.at(pw);
        let nearest = (0..centers.len())
            .min_by(|&a, &b| (centers[a] - frac).abs().total_cmp(&(centers[b] - frac).abs()))
            .expect("a nominal axis has categories");
        (starts[nearest] + bandwidth / 2.0) / pw
    }
}

impl BarX<'_> {
    /// A band x's bandwidth as a fraction of a plot `pw` wide: a category scale's
    /// or an ordinal date scale's.
    fn bandwidth(&self, pw: f64) -> Option<f64> {
        if let (Some(_), Some(round)) = (self.bandwidth, &self.nominal_round) {
            return Some(round.at(pw).1 / pw);
        }
        self.bandwidth.or_else(|| self.temporal.and_then(|axis| axis.bandwidth()))
    }

    /// The `(left, width, label_x)` of the rect of `row`, whose x sits at
    /// `center` on a plot `pw` wide: a time bucket's inner 80% (`bandPosition`
    /// with Vega-Lite's 0.5px crisp offsets), a band's `band_width` share, or on
    /// a quantitative x the continuous bar ladder over the layer's own closest
    /// x values. A bucketed bar's value labels sit at the bucket's own start. A layer authoring its own
    /// `x` carries no time unit, so its bars take the continuous ladder.
    fn slot(
        &self,
        bl: &OverlayBarLayer,
        (rows, x_field): (&[serde_json::Map<String, Value>], &str),
        (row, center): (&serde_json::Map<String, Value>, f64),
        pw: f64,
        bucketed: bool,
    ) -> Result<(f64, f64, f64), String> {
        if let Some(bandwidth) = self.bandwidth(pw) {
            let bw = bandwidth * pw;
            let width = (bw * bl.bar_mark.band_width).min(bw);
            return Ok((center - width / 2.0, width, center));
        }
        if let Some(axis) = self.temporal {
            let span = axis.bucket_span(&row_str(row, x_field), pw);
            let Some((start, end)) = span.filter(|_| bucketed) else {
                // No calendar grain buckets the scale: the continuous ladder over
                // the layer's own closest dates, in days.
                let (lo, hi) = axis
                    .day_domain(pw)
                    .ok_or("scatter: a bar layer on a date x needs a bucketed or continuous date axis")?;
                let days = |row: &serde_json::Map<String, Value>| axis.date_day(&row_str(row, x_field));
                let step_days = crate::families::bar::min_gap(rows.iter().filter_map(days).collect());
                let width = crate::families::bar::ContinuousWidth::of(&bl.bar_mark)?.at(step_days * pw / (hi - lo));
                // An authored x seats the bar mid-bucket on a bucketed axis (`bandPosition` 0.5).
                let center = span.map_or(center, |(start, end)| (start + end) / 2.0 * pw);
                return Ok((center - width / 2.0, width, center));
            };
            let (left, width) = crate::families::bar::bucket_bar_extent(start * pw, end * pw, crate::families::bar::DEFAULT_BAND_WIDTH);
            return Ok((left, width, start * pw));
        }
        // The ladder reads the pixel gap between the layer's closest x values; a
        // linear scale's is its data gap over the domain's span.
        let width = match (self.linear, self.log) {
            (Some(lin), _) => {
                let (lo, hi) = lin.domain_at(pw);
                let step = crate::families::bar::min_adjacent_step(rows, x_field);
                crate::families::bar::ContinuousWidth::of(&bl.bar_mark)?.at(step * pw / (hi - lo))
            }
            (None, Some(log)) => {
                let ladder = log.ladder(pw)?;
                let gap_px = crate::families::bar::min_gap(
                    rows.iter().filter_map(|row| row_num(row, x_field)).map(|v| ladder.frac_from_left(v) * pw).collect(),
                );
                crate::families::bar::ContinuousWidth::of(&bl.bar_mark)?.at(gap_px)
            }
            (None, None) => unreachable!("a bar layer's x is quantitative, log, band or a date"),
        };
        Ok((center - width / 2.0, width, center))
    }
}

/// A category's text on an axis or tooltip: Vega stringifies a null category
/// as `null`.
fn category_text(row: &serde_json::Map<String, Value>, field: &str) -> String {
    match row.get(field) {
        None | Some(Value::Null) => "null".to_string(),
        Some(_) => row_str(row, field),
    }
}

/// Vega's point-scale domain for a categorical channel: the distinct values
/// in the order the compiled scale builds them. An authored `sort` compiles
/// to `{op: "min", field, order}` over the UNFILTERED rows (`source_0`) and
/// sorts stably, so ties keep first appearance; with no sort Vega's
/// `sort: true` orders the mark rows' own values (`data_0`) ascending.
fn point_scale_domain(
    all_rows: &[serde_json::Map<String, Value>],
    kept: &[(f64, &serde_json::Map<String, Value>)],
    field: &str,
    sort: Option<&ChartSort>,
) -> Result<Vec<String>, String> {
    let source: Vec<&serde_json::Map<String, Value>> = match sort {
        Some(_) => all_rows.iter().collect(),
        None => kept.iter().map(|&(_, row)| row).collect(),
    };
    let mut domain: Vec<String> = Vec::new();
    for row in &source {
        let value = category_text(row, field);
        if !domain.contains(&value) {
            domain.push(value);
        }
    }
    let Some(sort) = sort else {
        let mut by_label: Vec<Value> = Vec::with_capacity(domain.len());
        for category in &domain {
            let row = source.iter().find(|row| category_text(row, field) == *category).expect("domain built from source");
            by_label.push(row.get(field).cloned().unwrap_or(Value::Null));
        }
        let ordered = crate::common::vega_ascending_sort(by_label).map_err(|e| format!("scatter: {e}"))?;
        return Ok(ordered
            .iter()
            .map(|v| match v {
                Value::String(s) => s.clone(),
                Value::Null => "null".to_string(),
                other => other.to_string(),
            })
            .collect());
    };
    crate::common::min_ranked_domain(domain, &source, |row| category_text(row, field), sort).map_err(|e| format!("scatter: {e}"))
}

/// The point mark's size scale range — Vega-Lite's own fixed default for a
/// quantitative size channel on a `point` mark, probe-confirmed independent
/// of `mark.size`, `mark.type`-implied config, and plot geometry (varied
/// width/height/mark.size, range never moved).
const SIZE_SCALE_RANGE: (f64, f64) = (4.0, 361.0);
/// `legend.label.padding` (VL's `labelOffset`) as production always
/// compiles it, the size legend's gap from its per-row symbol cell
/// (`size_legend_grid`) to the label.
const SIZE_LEGEND_LABEL_OFFSET: f64 = 8.0;

/// The `(x, y, size)` readings of a row Vega-Lite keeps, `None` for a dropped
/// one. `mark.invalid: "filter"` compiles to one `&&` over every quantitative
/// or temporal field the unit encodes (`filterinvalid.ts:36-55,85-99`): x and
/// y unless categorical, and size. A categorical axis adds no term, so a null
/// category stays (Vega draws it as its own `null` band; `render` refuses
/// it); a date x is kept when it has a value, the axis having refused any that is no date.
/// Every mark and legend reads the rows this keeps, and so does every domain
/// but y's, which a row with no valid x still joins (`y_when_x_invalid`).
fn kept_reading(
    row: &serde_json::Map<String, Value>,
    r: &ScatterResolved,
    x_nominal: bool,
    x_dates: bool,
    y_nominal: bool,
) -> Option<(f64, Option<f64>, Option<f64>)> {
    let x = x_reading(row, r, x_nominal, x_dates)?;
    let (y, size) = measure_reading(row, r, y_nominal)?;
    Some((x, y, size))
}

fn x_reading(row: &serde_json::Map<String, Value>, r: &ScatterResolved, x_nominal: bool, x_dates: bool) -> Option<f64> {
    Some(if x_nominal {
        0.0
    } else if x_dates {
        // A date x that is a value was read by the axis `bind` built, which refused any that is no date.
        (!row_str(row, &r.x).is_empty()).then_some(0.0)?
    } else {
        row_num(row, &r.x)?
    })
}

/// The readings of a row other than its x: the measure y, the size and the numeric color.
fn measure_reading(
    row: &serde_json::Map<String, Value>,
    r: &ScatterResolved,
    y_nominal: bool,
) -> Option<(Option<f64>, Option<f64>)> {
    let y = if y_nominal { None } else { Some(row_num(row, &r.y)?) };
    let size = match r.size.as_deref() {
        Some(size_field) => Some(row.get(size_field).and_then(crate::common::js_to_number).filter(|v| v.is_finite())?),
        None => None,
    };
    if let Some(color_field) = r.color.as_deref().filter(|_| quantitative_color(r)) {
        row_num(row, color_field).filter(|v| v.is_finite())?;
    }
    Some((y, size))
}

/// The y of a row whose x is the only invalid reading: it draws no mark, yet
/// `break-paths-show-domains` keeps it in the y domain.
fn y_when_x_invalid(
    row: &serde_json::Map<String, Value>,
    r: &ScatterResolved,
    x_nominal: bool,
    x_dates: bool,
    y_nominal: bool,
) -> Option<f64> {
    if x_reading(row, r, x_nominal, x_dates).is_some() {
        return None;
    }
    measure_reading(row, r, y_nominal)?.0
}

/// A numeric color column is a continuous scale: Vega-Lite's `blues` ramp
/// over the data extent, with a gradient legend. A wide chart's color is its
/// own nominal series label.
fn quantitative_color(r: &ScatterResolved) -> bool {
    r.wide_measures.is_empty()
        && r.resolved_channels.get("color").is_some_and(|ch| ch.is_continuous())
}

/// The `(min, max)` of a numeric color column over the drawn rows, which
/// `kept_reading` has already limited to numeric readings.
fn color_extent<'a>(
    rows: impl IntoIterator<Item = &'a serde_json::Map<String, Value>>,
    field: &str,
) -> (f64, f64) {
    rows.into_iter()
        .map(|row| row_num(row, field).expect("a drawn row carries its quantitative color reading"))
        .fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), v| (lo.min(v), hi.max(v)))
}

/// The indices of the rows `kept_reading` keeps.
fn drawn_at(
    rows: &[serde_json::Map<String, Value>],
    r: &ScatterResolved,
    x_nominal: bool,
    x_dates: bool,
    y_nominal: bool,
) -> Vec<usize> {
    (0..rows.len()).filter(|&i| kept_reading(&rows[i], r, x_nominal, x_dates, y_nominal).is_some()).collect()
}

/// `rows` at the indices `at`.
fn rows_at<'a>(rows: &'a [serde_json::Map<String, Value>], at: &[usize]) -> Vec<&'a serde_json::Map<String, Value>> {
    at.iter().map(|&i| &rows[i]).collect()
}

/// The linear x scale over the data extent `[x_min, x_max]`. An authored pair
/// reaches Vega verbatim and only an explicit `nice: true` rounds it;
/// otherwise the scale spans the data joined to zero (unless authored off) and
/// rounds out unless `nice: false`.
fn linear_x_scale(r: &ScatterResolved, x_min: f64, x_max: f64, rules: (bool, bool)) -> Result<LinearX, String> {
    Ok(LinearX::over(&r.style.axis_x, x_min, x_max, rules, !r.style.axis_x.is_zero_false(), true))
}

impl LinearX {
    /// The scale `ax_x` draws over the data extent `[x_min, x_max]`, with the threshold rules `rules` fires and
    /// the family's default for joining the domain to zero. `clear_edges` keeps a data-fit domain's
    /// non-zero ends off the data (scatter's law).
    pub(crate) fn over(ax_x: &AxisStyle, x_min: f64, x_max: f64, rules: (bool, bool), zero: bool, clear_edges: bool) -> Self {
        let authored = ax_x.coerced_continuous_domain();
        let nice = ax_x.scale.get("nice").and_then(Value::as_bool);
        let (raw, zero, nice) = match authored {
            Some(pair) => (pair, false, nice == Some(true)),
            None => ((x_min, x_max), zero, nice != Some(false)),
        };
        Self {
            raw,
            zero,
            nice,
            fit: clear_edges && authored.is_none() && !zero,
            pad: ax_x.scale.get("padding").and_then(Value::as_f64).unwrap_or(0.0),
            bend: ax_x.bend(),
            tick_count: ax_x.ticks.count.unwrap_or(6.0) as usize,
            min_step: ax_x.ticks.step,
            format: ax_x.labels.format.clone(),
            ruler: ax_x.ruler.clone(),
            tick_label: ax_x.tick_label.clone(),
            expr: ax_x.labels.expr.clone(),
            case: ax_x.labels.font.case.clone(),
            rule_datums: [(0.0, rules.0), (1.0, rules.1)].map(|(datum, fires)| fires.then_some(datum)),
        }
    }
}

/// A log x axis over the data extent on a plot `pw` wide, with each tick's
/// label: the authored format (else the default axis format), blank on a rung
/// d3 drops the mantissa of.
fn log_x_axis(ax_x: &AxisStyle, x_min: f64, x_max: f64, pw: f64) -> Result<(LogAxis, Vec<String>), String> {
    let x_tick_count = ax_x.ticks.count.unwrap_or(6.0) as usize;
    let ladder = LogAxis::new(ax_x, x_min, x_max, x_tick_count, ax_x.ticks.step, pw)?;
    let labels = ladder
        .ticks
        .iter()
        .enumerate()
        .map(|(i, &tv)| match (ladder.mantissa_visible[i], ax_x.labels.format.as_ref()) {
            (false, _) => Ok(String::new()),
            (true, slot) => {
                // Vega labels an unformatted log axis with d3's bare `,` specifier.
                let label = match slot {
                    Some(slot) if ax_x.labels.expr.is_some() => crate::number_locale::d3_format(&slot.spec, tv),
                    Some(slot) => slot.paint_tick(tv),
                    None => crate::number_locale::d3_format(",", tv),
                }
                .map_err(|e| format!("scatter x label format: {e}"))?;
                ax_x.labels.apply_expr("x", tv, label, i, ladder.ticks.len())
            }
        })
        .collect::<Result<_, String>>()?;
    Ok((ladder, labels))
}

/// d3's point-scale positions of `n` categories as fractions of the plot
/// width: `step = 1 / max(1, n - 1 + 2·padding)` with d3's default
/// `align: 0.5`. The theme bakes `axis_x.scale.padding: 0`; unlike y's dot
/// plot, which keeps VL's own raw default of 0.5.
fn point_x_fracs(padding: f64, n: usize) -> Vec<f64> {
    let n = n as f64;
    let step = 1.0 / (n - 1.0 + 2.0 * padding).max(1.0);
    let start_adj = (1.0 - step * (n - 1.0)) * 0.5;
    (0..n as usize).map(|i| start_adj + step * i as f64).collect()
}

/// The band starts and bandwidth, in pixels, of `n` categories on a rounded d3
/// band scale (a point scale is `inner = 1`) over `pw` pixels: the step floors
/// and the start and bandwidth round.
fn round_band_positions(pw: f64, n: usize, inner: f64, outer: f64) -> (Vec<f64>, f64) {
    let n = n as f64;
    let step = (pw / (n - inner + 2.0 * outer).max(1.0)).floor();
    let start = ((pw - step * (n - inner)) * 0.5).round();
    ((0..n as usize).map(|i| start + step * i as f64).collect(), (step * (1.0 - inner)).round())
}

fn point_x_padding(ax_x: &AxisStyle) -> f64 {
    ax_x.scale.get("padding").and_then(Value::as_f64).unwrap_or(0.5)
}

/// The mark fractions of `n` categories on an x scale, and, when a layer that needs a band
/// scale (a bar, a step line) or a mirror ghost makes it one, the band centers it places
/// them at and its bandwidth as a fraction of the plot width.
fn point_x_scale(ax_x: &AxisStyle, n: usize, band: bool) -> (Vec<f64>, Option<f64>) {
    if !band {
        return (point_x_fracs(point_x_padding(ax_x), n), None);
    }
    let padding = point_x_padding(ax_x);
    let geometry = crate::families::cartesian::BandGeometry::new(n, 0.0, 1.0, padding, padding);
    ((0..n).map(|i| geometry.edge(i, 0.5)).collect(), Some(geometry.bandwidth()))
}

/// What `paint_plain_x_axis` draws: the ticks (plot-width fractions) and labels
/// of a categorical or numeric x axis against a plot at `plot_left` whose
/// bottom edge is `plot_bottom`.
struct PlainXPaint<'a> {
    ax_x: &'a AxisStyle,
    ticks: &'a [f64],
    labels: &'a [String],
    plot_left: f64,
    plot_w: f64,
    plot_bottom: f64,
    /// The y of the plot's own edge, where the ticks start: `plot_bottom` itself unless a facet footer sits below it.
    floor_y: f64,
    /// The stroke of the baseline rule on the plot edge the axis hangs from, which its ticks continue.
    floor_rule: Option<&'a str>,
    /// The vertical threshold rules a tick at their value continues, in plot coordinates.
    x_rules: &'a [crate::common::ThresholdRule<'a>],
    angle: f64,
    align: &'a str,
    baseline: &'a str,
    /// A continuous scale flushes its end labels; a point scale centers every one.
    flush: bool,
    /// Where a point scale's labels sit against the axis group's crisp half
    /// pixel: a single chart paints them past it, a facet's footer on the band
    /// center (`scale(d) - 0.5` under it).
    point_label_crisp: f64,
    title: &'a str,
    caption: &'a AxisCaption,
    title_key: Option<crate::contract::LabelKey>,
}

/// A categorical or numeric x axis: ticks, labels and title, placed in a
/// single chart's plot group or a facet's footer band.
fn plain_x_axis(p: &PlainXPaint, fonts: &FontBook) -> Result<Vec<Node>, String> {
    let ax_x = p.ax_x;
    let x_font_size = ax_x.labels.font.size_or(11.0);
    let x_family = ax_x.labels.font.family_or("sans-serif");
    let x_label_pad = ax_x.labels.padding.unwrap_or(6.0);
    let edge = ax_x.x_edge();
    let sign = edge.sign();
    // `plot_bottom` is the y of the plot edge the axis hangs from. Vega's crisp half pixel shifts the group down on either edge.
    let x_axis_drop = sign * ax_x.ticks.offset.unwrap_or(0.0) + 0.5;
    let x_ticks_visible = ax_x.ticks.visible.unwrap_or(true);
    let x_tick_len = ax_x.ticks.length.unwrap_or(6.0);
    let x_tick_eff = if x_ticks_visible { x_tick_len } else { 0.0 };
    let x_limit = ax_x.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    let (plot_left, plot_w, plot_bottom) = (p.plot_left, p.plot_w, p.plot_bottom);
    let x_pos = |frac: f64| frac * plot_w;
    let label_crisp = if p.flush { 0.5 } else { p.point_label_crisp };
    // Only a lone chart's axis: a facet's shared footer is laid out by the composer.
    if crate::labels_expr::view::active() && p.title_key.is_some() {
        use crate::labels_expr::view::{anchor, register_axis, AxisSpec, Orient};
        let (align, baseline, flush) = if p.angle == 0.0 {
            (None, None, p.flush.then_some(1.0))
        } else {
            (Some(anchor(p.align)?), Some(anchor(p.baseline)?), None)
        };
        let spec = AxisSpec { orient: Orient::Bottom, offset: x_tick_eff + x_label_pad, shift: label_crisp - 0.5, align, baseline, angle: p.angle, flush, style: ax_x.labels.item_style()? };
        register_axis("x", spec);
    }
    let mut items = Vec::new();
    if x_ticks_visible {
        let xs = p.ticks.iter().map(|&frac| plot_left + x_pos(frac).round() + 0.5);
        let end_y = plot_bottom + x_axis_drop + sign * x_tick_len;
        let rules: Vec<_> = p.x_rules.iter().map(|rule| crate::common::ThresholdRule { at: plot_left + rule.at, tick_at: plot_left + rule.tick_at, ..*rule }).collect();
        items.extend(crate::common::x_tick_marks(&ax_x.ticks, p.floor_rule, &rules, xs, (p.floor_y, end_y), 3)?);
    }
    let x_label_color = ax_x.labels.font.color.as_deref().unwrap_or("#626366");
    let x_label_weight = ax_x.labels.font.weight_str();
    if p.angle != 0.0 {
        // Vega's SVG text transform for a rotated item: translate to the
        // anchor, rotate, then the baseline shift — bar's own
        // `BandXAxis::paint` rotated arm (`bar.rs`), reused verbatim.
        let anchor_y = plot_bottom + x_axis_drop + sign * (x_tick_eff + x_label_pad);
        let dy = measure::baseline_shift(p.baseline, x_font_size);
        let text_anchor = match p.align {
            "left" => "start",
            "right" => "end",
            _ => "middle",
        };
        for (&frac, lbl) in p.ticks.iter().zip(p.labels) {
            let pos = plot_left + x_pos(frac) + label_crisp;
            items.push(Node::text(
                Attrs::new()
                    .set("text-anchor", text_anchor)
                    .set("transform", format!("translate({pos:.3},{anchor_y:.3}) rotate({}) translate(0,{dy:.3})", p.angle))
                    .vega("font-family", x_family)
                    .set("font-size", x_font_size.to_string())
                    .set("font-weight", x_label_weight.as_str())
                    .set("fill", x_label_color),
                ax_x.labels.clip(lbl, fonts)?,
            ));
        }
    } else {
        let x_label_y = plot_bottom + x_axis_drop + sign * (x_tick_eff + x_label_pad) + measure::baseline_shift(edge.baseline(), x_font_size);
        let x_flush_align = |pos: f64, pw: f64| if p.flush { crate::families::line::flush_align(pos, pw) } else { "center" };
        let x_label_visible = ax_x.is_quantitative.then(|| {
            let widths: Result<Vec<f64>, String> =
                p.labels.iter().map(|lbl| fonts.measure(lbl, x_family, x_font_size)).collect();
            let widths = widths?;
            Ok::<Vec<bool>, String>(visible_tick_labels(p.labels, ax_x.labels.min_gap.unwrap_or(0.0), ax_x.is_log_scale(), |i| {
                let pos = p.ticks[i] * plot_w;
                vega_fit::text_ink_rows(
                    pos,
                    plot_bottom + x_axis_drop + sign * (x_tick_eff + x_label_pad),
                    widths[i].min(x_limit),
                    x_font_size,
                    x_flush_align(pos, plot_w),
                    edge.baseline(),
                    crate::text::line_count(&p.labels[i]),
                )
            }))
        });
        let x_label_visible = x_label_visible.transpose()?;
        for (i, (&frac, lbl)) in p.ticks.iter().zip(p.labels).enumerate() {
            if x_label_visible.as_ref().is_some_and(|v| !v[i]) {
                continue;
            }
            let pos = plot_left + x_pos(frac) + label_crisp;
            let anchor = if p.flush { crate::families::line::flush_anchor_px(pos - plot_left - label_crisp, plot_w) } else { "middle" };
            items.push(Node::text(
                Attrs::new()
                    .set("x", format!("{pos:.3}"))
                    .set("y", format!("{x_label_y:.3}"))
                    .set("text-anchor", anchor)
                    .vega("font-family", x_family)
                    .set("font-size", x_font_size.to_string())
                    .set("font-weight", x_label_weight.as_str())
                    .set("fill", x_label_color),
                ax_x.labels.clip(lbl, fonts)?,
            ));
        }
    }

    let x_title_band = crate::common::axis_title_band(&ax_x.title, p.title);
    if p.angle != 0.0 && x_title_band > 0.0 {
        // Vega's `axisTitleLayout` offsets the title by the label ink's reach past
        // the pivot plus `titlePadding`, read from `rotated_category_label_ink`.
        let anchor_y = plot_bottom + x_axis_drop + sign * (x_tick_eff + x_label_pad);
        let reach = p.labels.iter().try_fold(0.0_f64, |acc, lbl| {
            let w = fonts.measure_shaped(lbl, x_family, x_font_size)?.min(x_limit);
            let ink = crate::common::rotated_category_label_ink(0.0, 0.0, w, x_font_size, p.align, p.baseline, p.angle, crate::text::line_count(lbl) - 1);
            Ok::<f64, String>(acc.max(match edge {
                crate::common::XEdge::Bottom => ink.y2,
                crate::common::XEdge::Top => -ink.y1,
            }))
        })?;
        let title_fs = ax_x.title.font.size_or(11.0);
        let title_padding = ax_x.title.padding.unwrap_or(10.0);
        let title_y = anchor_y + sign * (reach + title_padding) + measure::baseline_shift(edge.baseline(), title_fs);
        items.extend(crate::common::axis_title_at(p.title, &ax_x.title, plot_left + plot_w / 2.0, title_y, p.title_key));
    } else if p.ticks.is_empty() {
        // An axis with no ticks has no label bounds to clear: the title sits one padding below the axis origin.
        let title_fs = ax_x.title.font.size_or(11.0);
        let title_y = plot_bottom + x_axis_drop + sign * ax_x.title.padding.unwrap_or(10.0) + measure::baseline_shift(edge.baseline(), title_fs);
        items.extend(crate::common::axis_title_at(p.title, &ax_x.title, plot_left + plot_w / 2.0, title_y, p.title_key));
    } else {
        let label_rows = p.labels.iter().map(|l| crate::text::line_count(l)).max().unwrap_or(1);
        let x_label_baseline = plot_bottom
            + x_axis_drop
            + sign * (x_tick_eff + x_label_pad)
            + measure::baseline_shift(edge.baseline(), x_font_size)
            + sign * (label_rows - 1) as f64 * crate::text::line_height(x_font_size);
        items.extend(crate::common::axis_title_flat(p.title, &ax_x.title, plot_left + plot_w / 2.0, x_label_baseline, x_font_size, p.title_key, edge));
    }
    crate::contract::axis_group(crate::contract::AxisChannel::X, p.caption, (plot_left + 0.5, plot_bottom + x_axis_drop), items)
}

/// A facet's shared categorical or numeric x axis, painted once below the last
/// row: one axis for the whole chart, laid out against every row's data.
#[derive(Clone)]
pub(crate) struct PlainXAxis {
    scale: PlainXScale,
    angle: f64,
    align: &'static str,
    baseline: &'static str,
    title: String,
    /// How far a label's ink reaches left of the plot origin, at the plot
    /// width `sized_for` measured it against.
    left_reach: f64,
    /// How far a rotated label column reaches below the plot bottom.
    rotated_reach: Option<f64>,
    /// `scale.round`: a continuous scale rounds each position to a pixel.
    round: bool,
    /// `scale.round` on a category scale: d3's floored step and rounded start.
    nominal_round: Option<NominalRound>,
    /// The stroke of the baseline rule on the plot edge this axis hangs from, which its ticks continue;
    /// the arm that paints the rule sets it.
    floor_rule: Option<String>,
    /// The vertical threshold rules this axis's ticks continue, in plot coordinates; the arm that paints them sets it.
    x_rules: Vec<crate::common::PaintedRule>,
}

#[derive(Clone)]
enum PlainXScale {
    /// A point scale over the sorted distinct categories (the layers' union in paint order),
    /// or a band scale beside a band-needing layer, whose `bandwidth` is a fraction of the plot.
    Point { domain: Vec<String>, shown: Vec<String>, fracs: Vec<f64>, bandwidth: Option<f64>, padding: f64 },
    Linear(LinearX),
    Log { ax: Box<AxisStyle>, extent: (f64, f64) },
}

impl PlainXAxis {
    /// The axis over the rows Vega keeps, its categories' tilt decided against
    /// `crowd_w`, the width production's crowding pass measures.
    fn resolve(
        r: &ScatterResolved,
        all_rows: &[serde_json::Map<String, Value>],
        drawn: &[&serde_json::Map<String, Value>],
        fires: &RuleFires,
        crowd_w: f64,
        title: String,
        mirrored: bool,
        layers: &XLayers,
        fonts: &FontBook,
    ) -> Result<Self, String> {
        let ax_x = &r.style.axis_x;
        let mut axis = Self {
            scale: PlainXScale::Linear(LinearX {
                raw: (0.0, 1.0),
                zero: false,
                nice: false,
                fit: false,
                pad: 0.0,
                bend: vega_fit::Bend::Linear,
                tick_count: 0,
                min_step: None,
                format: None,
                ruler: None,
                tick_label: None,
                expr: None,
                case: None,
                rule_datums: [None, None],
            }),
            angle: 0.0,
            align: "center",
            baseline: "top",
            title,
            left_reach: 0.0,
            rotated_reach: None,
            round: ax_x.scale.get("round").and_then(Value::as_bool) == Some(true),
            nominal_round: None,
            floor_rule: None,
            x_rules: Vec::new(),
        };
        if infer_vega_type(all_rows, &r.x) != VegaType::Quantitative {
            let kept: Vec<(f64, &serde_json::Map<String, Value>)> = drawn.iter().map(|&row| (0.0, row)).collect();
            let domain = if layers.columns.is_empty() {
                point_scale_domain(all_rows, &kept, &r.x, None)?
            } else {
                // The layers' union scale lists its categories in paint order, never sorted.
                let mut seen: Vec<String> = Vec::new();
                for &(_, row) in &kept {
                    let category = category_text(row, &r.x);
                    if !seen.contains(&category) {
                        seen.push(category);
                    }
                }
                crate::families::layers::union_x_domain(&seen, &layers.columns, false).domain
            };
            // The mirror ghost's invisible rule mark shares the x field and turns
            // the point scale into a band scale: marks sit at the band centers.
            let (fracs, bandwidth) = point_x_scale(ax_x, domain.len(), mirrored || layers.band);
            // An authored `labels.angle` short-circuits the crowding ladder.
            let layout = match ax_x.labels.angle {
                Some(angle) => vega_fit::CategoryLabels { angle, collision: None },
                None => vega_fit::category_label_layout(
                    &ax_x.labels,
                    &domain,
                    crowd_w,
                    r.style.label_usable_ratio,
                    fonts,
                    ax_x.labels.font.size_or(11.0),
                )?,
            };
            if let Some(label_count) = layout.collision {
                crate::families::cartesian::warn_axis_label_collision(&r.id, &r.x, label_count);
            }
            axis.angle = vega_fit::normalize_angle(layout.angle);
            (axis.align, axis.baseline) =
                if axis.angle == 0.0 { ("center", "top") } else { vega_fit::vl_x_label_anchor(axis.angle, true) };
            if axis.round {
                // `point_x_scale` builds a band scale when mirrored or beside a band layer.
                let padding = point_x_padding(ax_x);
                let inner = if mirrored || layers.band { padding } else { 1.0 };
                axis.nominal_round = Some(NominalRound { inner, outer: padding, n: domain.len() });
            }
            let shown = crate::labels_expr::band_texts("x", &domain, &ax_x.labels).map_err(|e| format!("scatter: {e}"))?;
            axis.scale = PlainXScale::Point { domain, shown, fracs, bandwidth: bandwidth.filter(|_| layers.band), padding: point_x_padding(ax_x) };
            return Ok(axis);
        }
        // Every layer's x column joins the scale's extent.
        let (x_min, x_max) = layers
            .columns
            .iter()
            .map(|&(rows, field)| column_extent(rows, field))
            .fold(column_extent(drawn.iter().copied(), &r.x), |(lo, hi), (l, h)| (lo.min(l), hi.max(h)));
        // Annotation numbers join the extent as data does.
        let (x_min, x_max) = crate::annotation::widen_numbers((x_min, x_max), &r.annotations);
        axis.scale = if ax_x.is_quantitative && ax_x.is_log_scale() {
            PlainXScale::Log { ax: Box::new(ax_x.clone()), extent: (x_min, x_max) }
        } else {
            PlainXScale::Linear(linear_x_scale(r, x_min, x_max, (fires.x_zero, fires.x_unity))?)
        };
        Ok(axis)
    }

    /// Registers the x scale for the `labels.expr`s that read it, over a plot `pw` wide.
    fn register(&self, pw: f64) -> Result<(), String> {
        use crate::labels_expr::view::{register_band, register_bent, register_log, register_point_padded, Span};
        match &self.scale {
            PlainXScale::Point { domain, bandwidth: Some(_), padding, .. } => register_band("x", domain, *padding, *padding, Span::Width)?,
            PlainXScale::Point { domain, padding, .. } => register_point_padded("x", domain, *padding, Span::Width)?,
            PlainXScale::Linear(lin) => {
                let (lo, hi) = lin.domain_at(pw);
                register_bent("x", lin.bend, lo, hi, Span::Width);
            }
            PlainXScale::Log { ax, extent } => {
                let (ladder, _) = log_x_axis(ax, extent.0, extent.1, pw)?;
                register_log("x", ladder.lo, ladder.hi, Span::Width);
            }
        }
        Ok(())
    }

    /// The ticks (plot-width fractions), their labels and the threshold rules'
    /// fractions at a plot `pw` wide.
    /// The scale Vega's axis description reads, on a plot `pw` wide.
    fn caption_scale(&self, ax_x: &AxisStyle, pw: f64) -> Result<CaptionScale, String> {
        Ok(match &self.scale {
            PlainXScale::Point { domain, .. } => {
                CaptionScale::Discrete { values: domain.clone(), format: vega_axis_format(ax_x, false)? }
            }
            PlainXScale::Linear(lin) => CaptionScale::Numeric {
                kind: NumericScale::of_axis(ax_x)?,
                domain: lin.domain_at(pw),
                format: vega_axis_format(ax_x, true)?,
            },
            PlainXScale::Log { ax, extent } => {
                let ladder = log_x_axis(ax, extent.0, extent.1, pw)?.0;
                CaptionScale::Numeric {
                    kind: NumericScale::Log,
                    domain: (ladder.lo, ladder.hi),
                    format: vega_axis_format(ax_x, true)?,
                }
            }
        })
    }

    pub(crate) fn ticks_at(&self, pw: f64) -> Result<(Vec<f64>, Vec<String>, [Option<f64>; 2]), String> {
        let (mut ticks, labels, mut rules) = match &self.scale {
            PlainXScale::Point { shown, fracs, .. } => (fracs.clone(), shown.clone(), [None, None]),
            PlainXScale::Linear(lin) => lin.at(pw).map(|(ticks, labels, rules, _)| (ticks, labels, rules))?,
            PlainXScale::Log { ax, extent } => {
                let (ladder, labels) = log_x_axis(ax, extent.0, extent.1, pw)?;
                (ladder.ticks.iter().map(|&tv| ladder.frac_from_left(tv)).collect(), labels, [None, None])
            }
        };
        if let Some(round) = &self.nominal_round {
            let centers = ticks.clone();
            ticks.iter_mut().for_each(|t| *t = round.snap(*t, &centers, pw));
        } else if self.round {
            let round = |frac: &mut f64| *frac = (*frac * pw).round() / pw;
            ticks.iter_mut().for_each(round);
            rules.iter_mut().flatten().for_each(round);
        }
        Ok((ticks, labels, rules))
    }

    /// The plot-width fraction one kept row lands on, `xv` its numeric x.
    pub(crate) fn frac(&self, xv: f64, row: &serde_json::Map<String, Value>, field: &str, pw: f64) -> Result<f64, String> {
        match &self.scale {
            PlainXScale::Point { domain, fracs, .. } => {
                let category = category_text(row, field);
                domain
                    .iter()
                    .position(|d| *d == category)
                    .map(|i| fracs[i])
                    .ok_or_else(|| format!("scatter: x value {category:?} is outside the point-scale domain"))
            }
            PlainXScale::Linear(lin) => Ok(lin.frac(lin.domain_at(pw), xv)),
            PlainXScale::Log { ax, extent } => Ok(log_x_axis(ax, extent.0, extent.1, pw)?.0.frac_from_left(xv)),
        }
    }

    /// The plot-width fraction a layer row lands on; `None` where the row has no position on the scale.
    pub(crate) fn layer_frac(&self, row: &serde_json::Map<String, Value>, field: &str, pw: f64) -> Option<f64> {
        match &self.scale {
            PlainXScale::Point { domain, fracs, .. } => {
                let category = category_text(row, field);
                domain.iter().position(|d| *d == category).map(|i| fracs[i])
            }
            PlainXScale::Linear(lin) => row_num(row, field).map(|v| lin.frac(lin.domain_at(pw), v)),
            PlainXScale::Log { ax, extent } => {
                let ladder = log_x_axis(ax, extent.0, extent.1, pw).ok()?.0;
                row_num(row, field).map(|v| ladder.frac_from_left(v))
            }
        }
    }

    /// The categories as the rows name them, whatever the axis paints for them.
    fn raw_domain(&self) -> Option<&[String]> {
        match &self.scale {
            PlainXScale::Point { domain, .. } => Some(domain),
            _ => None,
        }
    }

    fn linear(&self) -> Option<&LinearX> {
        match &self.scale {
            PlainXScale::Linear(lin) => Some(lin),
            _ => None,
        }
    }

    fn log(&self) -> Option<LogX<'_>> {
        match &self.scale {
            PlainXScale::Log { ax, extent } => Some(LogX { ax, extent: *extent }),
            _ => None,
        }
    }

    /// A category scale's unrounded band centers, as fractions of the plot width.
    fn category_centers(&self) -> &[f64] {
        match &self.scale {
            PlainXScale::Point { fracs, .. } => fracs,
            _ => &[],
        }
    }

    /// A band scale's bandwidth as a fraction of the plot width.
    fn bandwidth(&self) -> Option<f64> {
        match &self.scale {
            PlainXScale::Point { bandwidth, .. } => *bandwidth,
            _ => None,
        }
    }

    /// The fractions of a band scale's first and last band centers.
    fn band_ends(&self) -> Option<(f64, f64)> {
        match &self.scale {
            PlainXScale::Point { fracs, bandwidth: Some(_), .. } => Some((*fracs.first()?, *fracs.last()?)),
            _ => None,
        }
    }

    /// The axis with its label reaches measured for a plot `pw` wide.
    pub(crate) fn sized_for(&self, ax_x: &AxisStyle, pw: f64, fonts: &FontBook) -> Result<Self, String> {
        let mut sized = self.clone();
        let (ticks, labels, _) = self.ticks_at(pw)?;
        let (size, family) = (ax_x.labels.font.size_or(11.0), ax_x.labels.font.family_or("sans-serif"));
        let limit = ax_x.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
        let mut below = 0.0_f64;
        for (&frac, label) in ticks.iter().zip(&labels) {
            let w = fonts.measure_shaped(label, family, size)?.min(limit);
            if self.angle != 0.0 {
                let ink = crate::common::rotated_category_label_ink(
                    frac * pw, 0.0, w, size, self.align, self.baseline, self.angle, crate::text::line_count(label) - 1,
                );
                sized.left_reach = sized.left_reach.max(-ink.x1);
                below = below.max(ink.y2);
            } else if matches!(self.scale, PlainXScale::Point { .. }) {
                sized.left_reach = sized.left_reach.max(w / 2.0 - frac * pw);
            }
        }
        let tick_eff = if ax_x.ticks.visible.unwrap_or(true) { ax_x.ticks.length.unwrap_or(6.0) } else { 0.0 };
        sized.rotated_reach = (self.angle != 0.0)
            .then(|| ax_x.ticks.offset.unwrap_or(0.0) + tick_eff + ax_x.labels.padding.unwrap_or(6.0) + below);
        Ok(sized)
    }

    pub(crate) fn label_left_reach(&self) -> f64 {
        self.left_reach
    }

    pub(crate) fn rotated_footer_reach(&self) -> Option<f64> {
        self.rotated_reach
    }

    pub(crate) fn nodes(
        &self,
        ax_x: &AxisStyle,
        plot_left: f64,
        plot_w: f64,
        (plot_bottom, floor_y): (f64, f64),
        fonts: &FontBook,
    ) -> Result<Vec<Node>, String> {
        let (ticks, labels, _) = self.ticks_at(plot_w)?;
        let caption = AxisCaption::new(&self.title, &ax_x.title, self.caption_scale(ax_x, plot_w)?).bare_if_undrawn(ax_x);
        let x_rules = crate::common::thresholds(&self.x_rules);
        plain_x_axis(
            &PlainXPaint {
                ax_x,
                ticks: &ticks,
                labels: &labels,
                plot_left,
                plot_w,
                plot_bottom,
                floor_y,
                floor_rule: self.floor_rule.as_deref(),
                x_rules: &x_rules,
                angle: self.angle,
                align: self.align,
                baseline: self.baseline,
                flush: !matches!(self.scale, PlainXScale::Point { .. }),
                point_label_crisp: 0.0,
                title: &self.title,
                caption: &caption,
                title_key: None,
            },
            fonts,
        )
    }
}

/// The shared x axis a scatter facet's panels place their points against.
#[derive(Clone, Copy)]
pub(crate) enum FacetX<'a> {
    Date(&'a crate::families::scatter_axis::TemporalXAxis),
    Plain(&'a PlainXAxis),
}

/// The dates of a date x axis, as `(ladder, scale)`, left for the axis to order and dedupe:
/// production labels the axis from a ladder over every row's dates
/// (`emitters/_cartesian.py:416-418`, `emitters/_channels.py:1030-1031`) but
/// scales it to the rows Vega-Lite keeps (`filterinvalid.ts:36-55` runs after
/// it), so a date only a dropped row carries labels the axis without ever
/// placing a mark.
fn axis_dates(
    rows: &[serde_json::Map<String, Value>],
    drawn: &[&serde_json::Map<String, Value>],
    x_field: &str,
) -> (Vec<String>, Vec<String>) {
    let ladder = rows.iter().map(|row| row_str(row, x_field)).filter(|date| !date.is_empty()).collect();
    let scale = drawn.iter().map(|row| row_str(row, x_field)).collect();
    (ladder, scale)
}

/// The date x axis of the chart: every x value of its rows and of the layers' own columns, read once, and a
/// value that is no date refused. A null or empty x is no value. Empty without a date x.
fn x_time_axis(
    r: &ScatterResolved,
    payload: &Payload,
    overlays: &[OverlayLayer],
    x_dates: bool,
) -> Result<TimeAxis, Diagnostic> {
    if !x_dates {
        return Ok(TimeAxis::new(AxisGrain::default(), std::collections::HashMap::new()));
    }
    let panel_fields: Vec<&str> = r.panel_axes.iter().map(|a| a.field.as_str()).collect();
    let law = crate::families::x_type::resolve_cartesian_x_type(
        &payload.rows,
        &r.x,
        &crate::families::x_type::XAxis::of(&r.style.axis_x),
        "scatter",
        false,
        &panel_fields,
    )?;
    let grain = AxisGrain {
        unit: match law.grain {
            crate::families::x_type::Grain::Calendar(tu) => Some(tu),
            _ => None,
        },
        part: match law.grain {
            crate::families::x_type::Grain::Part(part) => Some(part),
            _ => None,
        },
        band: false,
    };
    let mut time = TimeAxis::new(grain, std::collections::HashMap::new());
    let mut columns: Vec<(&[serde_json::Map<String, Value>], &str)> = vec![(&payload.rows, &r.x)];
    for overlay in overlays {
        let rows = crate::families::layers::resolve_layer_rows(payload, overlay.query_name(), r.query_name.as_deref(), "scatter")?;
        columns.push((rows, overlay.x().unwrap_or(&r.x)));
    }
    for (rows, field) in columns {
        for row in rows {
            let date = row_str(row, field);
            if !date.is_empty() {
                time.add(&date).map_err(|e| e.diagnostic(&r.id, field, "scatter"))?;
            }
        }
    }
    Ok(time)
}

/// Whether a layer needs a band x scale, which Vega-Lite then compiles under the
/// base's marks too: a bar layer, or a literal-`step` line or area layer, which
/// doubles its rows onto the band edges (`_apply_layer_step_band`).
fn needs_band(overlay: &OverlayLayer) -> bool {
    match overlay {
        OverlayLayer::Bar(_) => true,
        OverlayLayer::Line(l) => l.curve_name() == Some("step"),
        OverlayLayer::Scatter(_) => false,
    }
}

/// A literal-`step` layer over a band x doubles its rows onto the band edges, which under a facet
/// reads the whole chart's rows rather than the panel's (`_apply_layer_step_band`).
fn reads_chart_rows(overlay: &OverlayLayer, x_is_band: bool) -> bool {
    x_is_band && matches!(overlay, OverlayLayer::Line(l) if l.curve_name() == Some("step"))
}

/// What the layers add to an x scale: each layer's own x column joins its
/// domain (Vega-Lite unions them), and on a date scale a bar layer spans its
/// time bucket, so the scale runs on to the end of the last one (`timeUnit`'s
/// `_end` joins the domain).
struct XLayers<'a> {
    columns: Vec<(&'a [serde_json::Map<String, Value>], &'a str)>,
    /// The bar layers' columns: their time buckets run the date scale on.
    bar_columns: Vec<(&'a [serde_json::Map<String, Value>], &'a str)>,
    /// A step line layer or a bar layer makes an ordinal scale a band scale.
    band: bool,
}

impl<'a> XLayers<'a> {
    fn of(
        r: &'a ScatterResolved,
        overlays: &'a [OverlayLayer],
        rows: &[&'a [serde_json::Map<String, Value>]],
    ) -> Self {
        XLayers {
            columns: overlays.iter().zip(rows).map(|(o, rows)| (*rows, o.x().unwrap_or(r.x.as_str()))).collect(),
            bar_columns: overlays
                .iter()
                .zip(rows)
                .filter(|(o, _)| matches!(o, OverlayLayer::Bar(_)))
                .map(|(o, rows)| (*rows, o.x().unwrap_or(r.x.as_str())))
                .collect(),
            band: overlays.iter().any(needs_band),
        }
    }
}

/// A date x axis for the chart's rows, laid out against a plot `slot_w`
/// wide (the width the ladder's crowding pass measures labels against).
/// Whether a date x is a continuous UTC scale over a calendar grain, where a year below 100 is
/// Vega's NaN position.
fn x_has_nan_years(payload: &Payload, r: &ScatterResolved) -> Result<bool, String> {
    let panel_fields: Vec<&str> = r.panel_axes.iter().map(|a| a.field.as_str()).collect();
    let law = crate::families::x_type::resolve_cartesian_x_type(
        &payload.rows,
        &r.x,
        &crate::families::x_type::XAxis::of(&r.style.axis_x),
        "scatter",
        false,
        &panel_fields,
    )?;
    Ok(law.vl_type == crate::families::temporal::VegaType::Temporal
        && matches!(law.grain, crate::families::x_type::Grain::Calendar(_)))
}

fn date_axis(
    payload: &Payload,
    r: &ScatterResolved,
    time: &TimeAxis,
    drawn: &[&serde_json::Map<String, Value>],
    layers: &XLayers,
    slot_w: f64,
    title_text: String,
    title_key: Option<crate::contract::LabelKey>,
    fonts: &FontBook,
) -> Result<crate::families::scatter_axis::TemporalXAxis, Diagnostic> {
    let usable_ratio = r
        .style
        .label_usable_ratio
        .ok_or("scatter: label_usable_ratio absent (theme required for a date x axis)")?;
    let mut time = time.clone();
    let unreadable = |e: crate::time_axis::Unreadable| e.diagnostic(&r.id, &r.x, "scatter");
    let (mut ladder, mut kept) = axis_dates(&payload.rows, drawn, &r.x);
    let layer_dates: Vec<String> = layers
        .columns
        .iter()
        .flat_map(|&(rows, field)| rows.iter().map(move |row| row_str(row, field)))
        .filter(|date| !date.is_empty())
        .collect();
    ladder.extend(layer_dates.iter().cloned());
    kept.extend(layer_dates);
    let panel_fields: Vec<&str> = r.panel_axes.iter().map(|a| a.field.as_str()).collect();
    let law = crate::families::x_type::resolve_cartesian_x_type(
        &payload.rows,
        &r.x,
        &crate::families::x_type::XAxis::of(&r.style.axis_x),
        "scatter",
        false,
        &panel_fields,
    )?;
    // Annotation dates join the domain as data does, except on a cyclic time
    // part, whose domain is not the calendar the annotation names. Their tick
    // dates join the ladder; the extent takes the days themselves.
    let mut extra_days: Vec<i64> = Vec::new();
    if !matches!(law.grain, crate::families::x_type::Grain::Part(_)) {
        extra_days.extend(crate::annotation::x_day_bounds(&r.annotations));
        for &days in &extra_days {
            let (y, m, d) = crate::families::temporal::civil_from_days(days);
            ladder.push(format!("{y:04}-{m:02}-{d:02}"));
        }
    }
    // An ordinal scale is a band scale beside a bar layer: its buckets are its own domain.
    let ordinal = law.vl_type == crate::families::temporal::VegaType::Ordinal;
    if let (false, crate::families::x_type::Grain::Calendar(_)) = (ordinal, law.grain) {
        // The scale runs on to the end of the last bucket a bar occupies, once that
        // passes every datum.
        let mut last_end: Option<f64> = None;
        for &(rows, field) in &layers.bar_columns {
            for row in rows {
                if let Some(end) = time.get(&row_str(row, field)).and_then(|instant| instant.end) {
                    last_end = Some(last_end.map_or(end, |last| last.max(end)));
                }
            }
        }
        let last_datum = kept.iter().filter_map(|d| time.get(d)).map(|instant| instant.at.floor()).reduce(f64::max);
        if let Some(end) = last_end.filter(|end| Some(*end) > last_datum) {
            let end = crate::families::temporal::instant_iso(end);
            ladder.push(end.clone());
            kept.push(end);
        }
    }
    for dates in [&mut ladder, &mut kept] {
        let mut seen = std::collections::HashSet::new();
        dates.retain(|date| seen.insert(date.clone()));
        for date in dates.iter() {
            time.add(date).map_err(unreadable)?;
        }
        time.sort_by_instant(dates);
    }
    let timed = crate::families::subday::SubdaySource::of(&time, ladder.iter().map(String::as_str));
    let mut axis = crate::families::scatter_axis::TemporalXAxis::resolve(
        &crate::families::scatter_axis::DateColumn { ladder: &ladder, kept: &kept, law, band_layer: layers.band, indexed: None, bucket_aligned: false, timed: &timed, chart_width: slot_w, extra_days: &extra_days, folded: false, time: &time },
        &r.style.axis_x,
        slot_w * usable_ratio,
        title_text,
        title_key,
        fonts,
    )?;
    if let Some(src) = r.style.axis_x.labels.expr.as_deref() {
        let expr = crate::labels_expr::Expr::parse(src).map(|e| e.on("x")).map_err(|e| format!("scatter: labels.expr {src:?}: {e:?}"))?;
        axis.speak(expr, &ladder)?;
    }
    Ok(axis)
}

/// The raw `(min, max)` of a numeric column across every payload row —
/// production's `numeric_column_values` scan (`baseline.py`), unconditioned
/// on any other channel parsing. `(f64::INFINITY, f64::NEG_INFINITY)` when
/// nothing parses. Shared by the y zero/unity gate and the x-threshold gate,
/// and by both `render_fit` and `render_legacy`, which must draw the SAME
/// rules production would, from the same raw extent.
///
/// The legacy arm's drawn scale domain is the same scan over the rows Vega
/// keeps (`legacy_measure_domain`): it passes only the rows that carry both
/// coordinates, the way `mark.invalid: "filter"` drops the rest.
fn column_extent<'a>(
    rows: impl IntoIterator<Item = &'a serde_json::Map<String, Value>>,
    field: &str,
) -> (f64, f64) {
    let mut lo = f64::INFINITY;
    let mut hi = f64::NEG_INFINITY;
    for row in rows {
        if let Some(v) = row_num(row, field) {
            lo = lo.min(v);
            hi = hi.max(v);
        }
    }
    (lo, hi)
}

/// Vega-Lite's default point size, the mark a shaped scatter's zero rule
/// layer carries.
const RULE_POINT_SIZE: f64 = 30.0;

/// A baseline rule's description: Vega's default `title: value` pair, no role
/// marker, at the tooltip format.
fn rule_aria(tooltip_labels: bool, spec: &str, title: &str, datum: f64) -> Result<Option<String>, String> {
    if !tooltip_labels {
        return Ok(None);
    }
    let v = crate::number_locale::d3_format(spec, datum)
        .map_err(|e| format!("scatter: baseline rule tooltip format: {e}"))?;
    Ok(Some(format!("{title}: {v}")))
}

/// Production's `BaselineFeature._insert_zero_rule` non-bar verdict for
/// scatter (`non_bar_zero_rule_should_fire`, combined with the log/grid/
/// authored-domain guards `build_zero_rule_if_applicable` shares across
/// every family — both in `baseline.py`). `y_data_min`/`y_data_max` are the
/// measure y's own raw extent (`column_extent`, unconditioned on x) —
/// production's shared-domain union, which collapses to this since scatter
/// carries no `layers` or fold/wide-measure path. Shared between
/// `render_fit` and `render_legacy` so the two arms cannot drift on the
/// same gate.
fn y_zero_rule_fires(ax_y: &AxisStyle, y_data_min: f64, y_data_max: f64, layered: bool) -> bool {
    let zero_in_shared_domain = || -> bool { y_data_min <= 0.0 && 0.0 <= y_data_max };
    let authored_y_domain = ax_y.authored_continuous_domain();
    // Under layers a baked edge pinned away from 0 overrules the rule as well:
    // the datum could not join the unioned domain.
    let excludes_zero = authored_y_domain.is_some_and(|(a, b)| !(a.min(b) <= 0.0 && 0.0 <= a.max(b)))
        || (layered
            && (ax_y.domain_min.is_some_and(|floor| floor > 0.0)
                || ax_y.domain_max.is_some_and(|ceiling| ceiling < 0.0)));
    // `build_zero_rule_if_applicable` skips a log scale: zero is off its domain.
    ax_y.is_quantitative
        && !ax_y.is_log_scale()
        && ax_y.grid.visible.unwrap_or(false)
        && ax_y.grid.threshold_visible()
        && !excludes_zero
        && if ax_y.is_zero_false() {
            zero_in_shared_domain()
        } else if authored_y_domain.is_none() {
            ax_y.zero_anchored || zero_in_shared_domain()
        } else {
            true
        }
}

/// The shape scale's domain, one law for the marks and the legend: the shape
/// field's categories, with Vega's `undefined` (sorted ahead of every string)
/// first when the y zero rule's layer carries a point mark.
fn shape_scale_domain<'a>(
    drawn: impl IntoIterator<Item = &'a serde_json::Map<String, Value>>,
    field: &str,
    rule_shape: bool,
) -> Vec<String> {
    let mut domain = color_domain_of(drawn, field);
    if rule_shape {
        domain.insert(0, "undefined".to_string());
    }
    domain
}

/// Which of `BaselineFeature`'s four `datum` rules fire over `rows`, read off
/// the raw columns (`numeric_column_values`).
struct RuleFires {
    y_zero: bool,
    y_unity: bool,
    x_zero: bool,
    x_unity: bool,
}

impl RuleFires {
    fn none() -> Self {
        Self { y_zero: false, y_unity: false, x_zero: false, x_unity: false }
    }

    /// `layer_y` is the extent the layers on the shared value scale add to the
    /// base's (`NO_EXTENT` for none).
    fn of(r: &ScatterResolved, rows: &[serde_json::Map<String, Value>], layer_y: (f64, f64), layered: bool) -> Self {
        let (ax_x, ax_y) = (&r.style.axis_x, &r.style.axis_y);
        let (y_min, y_max) = column_extent(rows, &r.y);
        let (x_min, x_max) = column_extent(rows, &r.x);
        let (y_min, y_max) = (y_min.min(layer_y.0), y_max.max(layer_y.1));
        Self {
            y_zero: y_zero_rule_fires(ax_y, y_min, y_max, layered),
            y_unity: crate::common::unity_rule_fires(ax_y, y_min, y_max),
            x_zero: crate::common::x_threshold_fires(ax_x, false, 0.0, x_min, x_max),
            x_unity: crate::common::x_threshold_fires(ax_x, true, 1.0, x_min, x_max),
        }
    }

    /// Vega-Lite gives every rule's layer a point mark carrying the shape
    /// scale's `undefined` category (the rule's row has no shape value), so a
    /// shape channel beside any firing rule gains that category.
    fn shape_layer(&self, r: &ScatterResolved) -> bool {
        r.shape.is_some() && (self.y_zero || self.y_unity || self.x_zero || self.x_unity)
    }
}

/// The extent of no values: the identity of a min/max union.
const NO_EXTENT: (f64, f64) = (f64::INFINITY, f64::NEG_INFINITY);

/// The color legend's entries as indices into `color_domain`, in drawn order.
/// An authored `legend.values` (`apply_legend_entry_order`,
/// `emitters/scatter.py:416-420`, and the wide fold's `_wide.py:206-210`)
/// picks and orders them; each keeps the palette slot its place in the
/// domain gave it. `raw_measures` are a wide fold's measure names, one per
/// domain entry, which resolve to their humanized labels
/// (`wide_legend_aliases`).
fn legend_entry_order(
    legend: &legend::LegendConfig,
    color_domain: &[String],
    raw_measures: Option<&[&str]>,
) -> Result<Vec<usize>, String> {
    match &legend.values {
        None => Ok((0..color_domain.len()).collect()),
        Some(authored) if authored.is_empty() => Ok(Vec::new()),
        Some(authored) => {
            let domain: Vec<&str> = color_domain.iter().map(String::as_str).collect();
            Ok(legend::resolve_legend_values(authored, &domain, raw_measures))
        }
    }
}

/// The color legend's drawn texts and layout: the shared Vega symbol legend
/// of unstroked points, which bounds the bare 10px glyph (every Vega symbol
/// at size 100 fits the r-5 circle's box, so a shape channel merged into the
/// legend keeps the circle's grid). The wide fold paints no title
/// (`_wide.py::fold_wide_measures` bakes `title: None`, which
/// `legend_title_text` reads off its synthetic color field).
fn color_legend_layout(
    r: &ScatterResolved,
    color_domain: &[String],
    order: &[usize],
    fonts: &FontBook,
    ring_glyphs: Option<&[&str]>,
    mirror: bool,
) -> Result<(Vec<String>, legend::VegaLegend), String> {
    let title = legend::legend_title_text(r.color.as_deref().unwrap_or(""), &r.legend, &r.field_labels)?;
    let labels = order.iter().map(|&idx| color_domain[idx].as_str());
    let limited = legend::limit_legend_text(labels, &title, &r.legend, fonts)?;
    let symbols: Vec<Option<legend::LegendSymbol>> = match ring_glyphs {
        None => vec![Some(if mirror { legend::LegendSymbol::Circle } else { legend::LegendSymbol::FilledCircle }); order.len()],
        Some(names) => order
            .iter()
            .map(|&idx| {
                Some(legend::LegendSymbol::Glyph(crate::families::scatter_shape::half_extents(names[idx], 5.0)))
            })
            .collect(),
    };
    let layout = legend::legend_from_widths(
        &limited.label_ws,
        &symbols, false,
        &limited.title,
        limited.title_w,
        &r.legend,
    )?;
    Ok((limited.labels, layout))
}

impl ScatterResolved {
    fn title_source(&self) -> TitleSource<'_> {
        TitleSource {
            family: "scatter",
            title: self.title.as_deref(),
            subtitle: self.subtitle.as_deref(),
            title_font: self.style.title_font.as_ref(),
            title_style: self.title_style.as_ref(),
        }
    }
}

/// A layered chart's mixed legend: the layers' shared `color:{datum}` scale,
/// each overlay claiming its label (or, grouped by color, its values) after the
/// base's own entries.
struct LayeredLegend {
    base: Vec<(String, String)>,
    scale: crate::families::layers::LayerScale,
    /// Per overlay: its legend label, color values, and the fill its claim took.
    plan: Vec<crate::families::layers::LayerClaim>,
    /// A line or area layer turns on the mixed legend, whose base circles are
    /// stroked; scatter and bar layers keep the base's bare ones.
    mixed: bool,
    /// A line layer makes the legend cell collapse to the font size even where
    /// its own entry is unlisted (a categorical y lists only scatter and bar layers).
    mixed_marks: bool,
    base_glyph: legend::LegendSymbol<'static>,
    /// Under a categorical y the base marks carry no color encoding, so the layers' own
    /// entries are all there is, and only a scatter or bar layer's draw one.
    only_filled: Option<HashSet<String>>,
    /// The border color a bar layer's legend square is stroked in, where the layers' own
    /// marks style the legend (a colorless dot plot).
    bar_borders: HashMap<String, String>,
    /// The color fields of the layers drawing on the shared scale.
    shared_color_fields: Vec<String>,
}

impl LayeredLegend {
    fn new(
        r: &ScatterResolved,
        payload: &Payload,
        overlays: &[OverlayLayer],
        color_domain: &[String],
        category_scale: Option<&crate::common::CategoryColorScale>,
        base_y_title: &str,
        y_nominal: bool,
    ) -> Result<Self, Diagnostic> {
        let colorless_dot_plot = y_nominal && r.color.is_none();
        let base: Vec<(String, String)> = if colorless_dot_plot {
            Vec::new()
        } else if r.color.is_some() {
            color_domain
                .iter()
                .map(|label| {
                    let fill = crate::common::series_color(label, color_domain, &r.series_palette, category_scale)?;
                    Ok((label.clone(), fill))
                })
                .collect::<Result<_, String>>()?
        } else {
            vec![(base_y_title.to_string(), r.style.single_series_fill.clone())]
        };
        let mut scale = crate::families::layers::LayerScale::new(&base, r.color.is_some());
        let layer_rows = overlays
            .iter()
            .map(|o| resolve_layer_rows(payload, o.query_name(), r.query_name.as_deref(), "scatter"))
            .collect::<Result<Vec<_>, _>>()?;
        let pairs: Vec<_> = overlays.iter().zip(layer_rows).collect();
        let shared_color_fields =
            crate::families::layers::shared_color_fields(pairs.iter().map(|&(overlay, rows)| (overlay, rows)))
                .into_iter()
                .map(str::to_string)
                .collect();
        let plan = claim_layers(&mut scale, &pairs, &r.field_labels)?;
        let mixed = !colorless_dot_plot && overlays.iter().any(|o| matches!(o, OverlayLayer::Line(_)));
        let base_glyph = if mixed { legend::LegendSymbol::Circle } else { legend::LegendSymbol::FilledCircle };
        let only_filled = colorless_dot_plot.then(|| {
            overlays
                .iter()
                .zip(&plan)
                .filter(|(o, _)| matches!(o, OverlayLayer::Scatter(_) | OverlayLayer::Bar(_)))
                .flat_map(|(_, claim)| std::iter::once(&claim.label).chain(&claim.values).cloned())
                .collect()
        });
        let bar_borders = overlays
            .iter()
            .zip(&plan)
            .filter_map(|(o, entry)| match o {
                OverlayLayer::Bar(bl) => Some((entry, bl.bar_mark.border.color.clone().unwrap_or_else(|| "none".into()))),
                OverlayLayer::Line(_) | OverlayLayer::Scatter(_) => None,
            })
            .filter(|_| colorless_dot_plot)
            .flat_map(|(claim, border)| {
                std::iter::once(&claim.label).chain(&claim.values).map(move |l| (l.clone(), border.clone())).collect::<Vec<_>>()
            })
            .collect();
        let mixed_marks = overlays.iter().any(|o| matches!(o, OverlayLayer::Line(l) if l.area_mark.is_none()));
        Ok(Self { base, scale, plan, mixed, mixed_marks, base_glyph, only_filled, bar_borders, shared_color_fields })
    }

    /// The legend's entries, narrowed and ordered by an authored `legend.values`.
    fn entries(&self, legend: &legend::LegendConfig) -> Vec<legend::LegendEntry<'_>> {
        legend::narrow_legend_entries(self.entries_unnarrowed(), legend)
    }

    fn entries_unnarrowed(&self) -> Vec<legend::LegendEntry<'_>> {
        self.scale
            .legend_entries(&self.base, |_| self.base_glyph)
            .into_iter()
            .map(|mut entry| {
                // Without a stroke legend no layer's glyph carries a stroke either.
                entry.symbol = match entry.symbol {
                    Some(legend::LegendSymbol::Square(_)) if self.bar_borders.contains_key(entry.label) => {
                        Some(legend::LegendSymbol::Square(&self.bar_borders[entry.label]))
                    }
                    Some(legend::LegendSymbol::Square(_)) if !self.mixed => Some(legend::LegendSymbol::FilledSquare),
                    Some(legend::LegendSymbol::FadedCircle(_)) if !self.mixed => Some(self.base_glyph),
                    other => other,
                };
                entry
            })
            .filter(|entry| self.only_filled.as_ref().is_none_or(|labels| labels.contains(entry.label)))
            .collect()
    }

    fn title(&self, r: &ScatterResolved) -> Result<String, String> {
        Ok(crate::families::layers::merged_legend_title(
            legend::legend_title_text(r.color.as_deref().unwrap_or(""), &r.legend, &r.field_labels)?,
            r.color.is_some(),
            self.shared_color_fields.iter().map(String::as_str),
            &r.legend,
        ))
    }
}

/// The x/y kinds and gates `bind` classifies the channels into.
struct Shape {
    x_dates: bool,
    x_nominal: bool,
    y_nominal: bool,
    y_measure: bool,
}

/// Small multiples: one fit-arm panel per baked partition cell, composed by
/// the shared facet composer. Scope: a date, category or numeric x against a
/// measure or categorical y, the value axis on either side. Every other shape refuses by name.
fn render_facet(bound: &bind::Bound, fonts: &FontBook) -> Result<String, Diagnostic> {
    let (payload, r, overlays, shape) = (&*bound.payload, &bound.r, bound.overlays.as_slice(), &bound.shape);
    let mc = r.multiples.as_ref().expect("render dispatched on multiples");
    let legend_cfg = &r.legend;
    if !shape.y_nominal && !shape.y_measure {
        return Err("scatter: multiples needs a baked measure y ladder".into());
    }
    let left_axis = r.style.axis_y.position.as_deref() == Some("left");
    let legend_edge = if (r.color.is_some() || r.size.is_some() || !overlays.is_empty()) && r.legend.visible {
        facet_compose::facet_legend_edge(&r.legend.position)
    } else {
        facet_compose::LegendEdge::Top
    };
    let independent = mc.scale == "independent";
    // Production's own compile fails on a layer that owns a value scale inside small
    // multiples (vl-convert: unrecognized scale `child_layer_0_y`), a categorical y's
    // layers included.
    if YSides::of(overlays).is_some() || (shape.y_nominal && !overlays.is_empty()) {
        return Err(crate::refuse!("scatter: multiples with a dual-axis layer not supported").into());
    }
    let ax_y = &r.style.axis_y;
    let has_mirror = ax_y.mirror_requested("scatter")?;
    if independent && has_mirror {
        return Err(multiples::independent_mirror_error(payload.chart_id()?));
    }
    if r.panel_axes.is_empty() {
        return Err("scatter: multiples with no baked panel_axes — resolve() should always supply at least one".into());
    }
    let axes = &r.panel_axes;
    let panels = multiples::regroup(axes, &payload.rows).map_err(|e| format!("scatter: {e}"))?;
    let panel_rows_n = multiples::panel_axis_cardinality(axes, mc.rows.as_deref());
    let panel_cols_n = multiples::panel_axis_cardinality(axes, mc.columns.as_deref());
    let missing = multiples::empty_leading_cells(panels.len(), panel_rows_n, panel_cols_n)?;
    let index_of = |field: &Option<String>| field.as_deref().and_then(|f| axes.iter().position(|a| a.field == f));

    // The date axis is resolved once, against the naive per-panel width the
    // emitter's own crowding pass measured (`RenderBox.width`), and shared by
    // every panel and by the composite's footer.
    let drawn = rows_at(&payload.rows, &bound.drawn);
    let naive_panel_w = multiples::facet_panel_width(payload.width, panel_cols_n, has_mirror, 0.0);
    // A categorical y narrows to each panel's own categories once some panel
    // holds a proper subset of them (`_domain_subset_narrowing_candidates`),
    // never beside a mirror and, beside columns, only while the extra axis
    // each panel then draws still leaves a legible panel.
    let y_extra_axis_px = if shape.y_nominal && !has_mirror && panel_cols_n > 1 {
        let every: Vec<(f64, &serde_json::Map<String, Value>)> = drawn.iter().map(|&row| (0.0, row)).collect();
        let domain = point_scale_domain(&payload.rows, &every, &r.y, r.sort.as_ref())?;
        crate::families::heatmap_facet::facet_extra_axis_px(&ax_y.labels, &domain, fonts)?
    } else {
        0.0
    };
    let y_narrowed = shape.y_nominal
        && !has_mirror
        && crate::families::heatmap_facet::panel_domain_is_proper_subset(&panels, axes, &r.y)
        && multiples::extra_axis_is_affordable(naive_panel_w, y_extra_axis_px);
    let extra_axis_px = if y_narrowed { y_extra_axis_px } else { 0.0 };
    let naive_panel_w = multiples::facet_panel_width(payload.width, panel_cols_n, has_mirror, extra_axis_px);
    let embedded_y = independent || y_narrowed;
    let title_boxes = multiples::facet_title_boxes((payload.width, payload.height), (panel_cols_n, panel_rows_n), has_mirror, extra_axis_px);
    let x_box = title_boxes.0;
    let x_title = crate::common::wrapped_axis_title(
        &r.id,
        "x_label",
        &crate::common::axis_title_text(&r.x, r.x_label.as_deref(), &r.field_labels)?,
        &r.style.axis_x.title.font,
        x_box.0,
        x_box.1,
        fonts,
    )?;
    let facet_layer_rows = overlays
        .iter()
        .map(|o| {
            panel_layer_rows(
                &payload.rows, true, reads_chart_rows(o, shape.x_nominal), payload, o.query_name(), r.query_name.as_deref(), "scatter",
            )
        })
        .collect::<Result<Vec<_>, _>>()?;
    let date_layers = XLayers::of(r, overlays, &facet_layer_rows);
    let date_x = shape
        .x_dates
        .then(|| date_axis(payload, r, &bound.time, &drawn, &date_layers, naive_panel_w, x_title.clone(), None, fonts))
        .transpose()?;
    if date_x.as_ref().is_some_and(crate::families::scatter_axis::TemporalXAxis::raises_no_marks) {
        return Err(crate::labels_expr::throw_diagnostic(payload));
    }
    if let Some(label_count) = date_x.as_ref().and_then(|axis| axis.collision) {
        crate::families::cartesian::warn_axis_label_collision(&r.id, &r.x, label_count);
    }
    // `BaselineFeature` skips a chart under an independent facet scale.
    let fires = if independent { RuleFires::none() } else { RuleFires::of(r, &payload.rows, NO_EXTENT, false) };
    let plain_x = (!shape.x_dates)
        .then(|| PlainXAxis::resolve(r, &payload.rows, &drawn, &fires, naive_panel_w, x_title, has_mirror, &date_layers, fonts))
        .transpose()?;
    let facet_x = match (&date_x, &plain_x) {
        (Some(axis), _) => FacetX::Date(axis),
        (None, Some(axis)) => FacetX::Plain(axis),
        (None, None) => unreachable!("an x axis is a date or a plain one"),
    };

    // A partition field the chart also encodes (`color: series` under
    // `rows: series`) stays a column of its own panel's rows in Vega-Lite;
    // `regroup` strips it, so it is restored from the panel's key.
    let panel_payloads: Vec<Payload> = panels
        .iter()
        .map(|panel| {
            let panel_rows = panel
                .rows
                .iter()
                .map(|row| {
                    let mut row = row.clone();
                    for (axis, value) in axes.iter().zip(&panel.key) {
                        row.insert(axis.field.clone(), value.clone());
                    }
                    row
                })
                .collect();
            payload.with_inputs(payload.resolved.clone(), panel_rows)
        })
        .collect();
    let panel_keys: Vec<String> =
        panels.iter().map(|panel| serde_json::to_string(&panel.key).expect("a panel key re-serializes")).collect();
    let render_panels = |plot_w: f64, plot_h: f64, value_axis_right_overhang: f64, left_overhang: f64| {
        panel_payloads
            .iter()
            .enumerate()
            .map(|(i, panel_payload)| {
                let opts = ScatterPanelOpts {
                    panel_key: &panel_keys[i],
                    panel_ordinal: i,
                    plot_w,
                    plot_h,
                    shared_rows: &payload.rows,
                    x_axis: facet_x,
                    paint_value_axis: embedded_y || (!left_axis && (missing + i) % panel_cols_n == panel_cols_n - 1),
                    fold_axis_gutter: embedded_y || (!left_axis && panel_cols_n == 1),
                    independent_scale: independent,
                    embedded_y,
                    value_axis_right_overhang,
                    left_overhang,
                    has_mirror,
                };
                let fitted = layout::fit(bound, panel_payload, fonts, Some(&opts), None)?;
                Ok((print::print(&[fitted.svg]), fitted.geometry.expect("a panel render carries its geometry")))
            })
            .collect::<Result<Vec<_>, Diagnostic>>()
    };
    // Every cell's group origin is its plot origin, so the composite places
    // one shared plot offset inside every panel canvas: the widest left ink
    // any panel measures (a panel whose marks start further right has less).
    let render_pass = |plot_w: f64, plot_h: f64, value_axis_right_overhang: f64| -> Result<_, Diagnostic> {
        let panels = render_panels(plot_w, plot_h, value_axis_right_overhang, 0.0)?;
        let left_overhang = panels.iter().map(|(_, g)| g.left_mark_overhang).fold(0.0_f64, f64::max);
        // A cell with its own left axis keeps that axis's reach to itself.
        if (embedded_y && left_axis) || panels.iter().all(|(_, g)| g.left_mark_overhang == left_overhang) {
            return Ok(panels);
        }
        Ok(render_panels(plot_w, plot_h, value_axis_right_overhang, left_overhang)?)
    };
    let header_font_family = r.legend.label.font.family.clone().unwrap_or_else(|| "sans-serif".into());

    let pad = payload.padding.as_ref();
    let avail_w = payload.width - pad.map_or(0.0, |p| p.left + p.right);
    let title_plan = match layout_title(&r.title_source(), fonts, avail_w)? {
        TitleLayout::Plan(plan) => {
            plan.warn_truncated(&r.id);
            Some(plan)
        }
        TitleLayout::Untitled => None,
    };
    // The legend is one row above the grid. The mirror layer's invisible
    // `rule` mark makes production's legend symbols stroke-bearing; without
    // it a point's symbol is fill only.
    let color_domain = &bound.color_domain;
    let category_scale = r.color.as_deref().and_then(|cf| crate::common::category_scale_for(&r.category_colors, cf));
    let legend_fills: Vec<String> = color_domain
        .iter()
        .map(|label| crate::common::series_color(label, &color_domain, &r.series_palette, category_scale))
        .collect::<Result<_, _>>()?;
    // A shape channel draws its own unconfigured legend on the right, except on
    // the color field, where it merges into the color legend with varying
    // glyphs (a rule layer's point keeps the two apart).
    let rule_shape = fires.shape_layer(r);
    let merged_shape = shape_merges_into_color(r, rule_shape, has_mirror);
    let shape_domain = r
        .shape
        .as_deref()
        .map(|field| shape_scale_domain(drawn.iter().copied(), field, rule_shape))
        .unwrap_or_default();
    let shape_legend = match r.shape.as_deref().filter(|_| !merged_shape) {
        Some(field) => {
            Some(crate::families::scatter_shape::ShapeLegend::new(fonts, payload, field.to_string(), shape_domain.clone())?)
        }
        None => None,
    };
    let unfilled = r.style.point_mark.filled == Some(false);
    let glyph_names = color_legend_glyph_names(r, color_domain.len(), merged_shape);
    let glyph_paths: Vec<String> = glyph_names
        .iter()
        .map(|name| crate::families::scatter_shape::path(name, 5.0))
        .collect::<Result<_, _>>()?;
    let entry_symbol = (!has_mirror).then_some(legend::LegendSymbol::FilledCircle);
    let legend_order = if r.color.is_some() && r.legend.visible {
        legend_entry_order(legend_cfg, &color_domain, None)?
    } else {
        Vec::new()
    };
    let legend_entries: Vec<legend::LegendEntry> = legend_order
        .iter()
        .map(|&idx| legend::LegendEntry {
            label: &color_domain[idx],
            fill: &legend_fills[idx],
            symbol: match r.style.point_mark.fill.as_deref().filter(|_| unfilled) {
                Some(interior) => Some(legend::LegendSymbol::Ring(
                    &glyph_paths[idx],
                    interior,
                    r.style.point_mark.opacity.unwrap_or(1.0),
                    crate::families::scatter_shape::half_extents(glyph_names[idx], 5.0),
                )),
                None => entry_symbol.map(|_| legend::LegendSymbol::FilledGlyph(&glyph_paths[idx])),
            },
            series_key: None,
            recede: None,
        })
        .collect();
    let legend_title = match r.color.as_deref() {
        Some(color) => legend::legend_title_text(color, legend_cfg, &r.field_labels)?,
        None => String::new(),
    };
    let base_y_title = crate::common::axis_title_text(&r.y, r.y_label.as_deref(), &r.field_labels)?;
    let layered_legend = LayeredLegend::new(r, payload, overlays, &color_domain, category_scale, &base_y_title, shape.y_nominal)?;
    let layered_entries = layered_legend.entries(&r.legend);
    let numeric_legends = crate::families::bar::legend::numeric_legends_of(
        None,
        overlays.iter().zip(&layered_legend.plan),
        legend_cfg,
        &r.id,
    )?;
    let layered = !overlays.is_empty();
    let vega_legend = if layered {
        (r.legend.visible && !quantitative_color(r))
            .then(|| legend::vega_legend(&layered_entries, layered_legend.mixed_marks, &layered_legend.title(r)?, legend_cfg, fonts))
            .transpose()?
    } else {
        (r.color.is_some() && r.legend.visible && !legend_entries.is_empty())
            .then(|| legend::vega_legend(&legend_entries, false, &legend_title, legend_cfg, fonts))
            .transpose()?
    };
    // A numeric color is a gradient legend whose bar follows the unit panel.
    let gradient_id = legend_gradient_id(r);
    let gradient_color = match r.color.as_deref().filter(|_| quantitative_color(r) && r.legend.visible) {
        Some(cf) => {
            let (lo, hi) = bound.color_range.expect("a quantitative color has its range");
            let scale = ContinuousColor::blues(lo, hi);
            let title = legend::legend_title_text(cf, legend_cfg, &r.field_labels)?;
            // Whatever can fail laying the legend out fails the same at every
            // size, so the per-size closures below cannot.
            GradientAt::measure(legend_cfg, r.color_format.as_ref(), &scale, (&gradient_id, &title), fonts, (1.0, 1.0))?;
            Some((scale, title))
        }
        None => None,
    };
    let size_domain = bound.size_domain;
    let size_legend = match size_domain.filter(|_| r.legend.visible) {
        Some(domain) => {
            let symbol = r.style.point_mark.shape.as_ref().and_then(Value::as_str).filter(|_| r.shape.is_none());
            Some(size_legend(r, fonts, domain, symbol.unwrap_or("circle"))?)
        }
        None => None,
    };
    let shape_fill = shape_legend_fill(r, &fires);
    let shape_opacity = r.style.point_mark.opacity.unwrap_or(1.0);
    let default_symbol = if has_mirror { legend::LegendSymbol::Circle } else { legend::LegendSymbol::FilledCircle };
    let shape_chrome = shape_legend.as_ref().map(|sl| facet_compose::LegendChrome {
        edge: facet_compose::LegendEdge::Right,
        width: Box::new(|_, _| sl.w),
        height: Box::new(|_| sl.h),
        paint: Box::new(|svg, x, y, _, _| {
            sl.paint(svg, x, y, shape_fill, shape_opacity);
        }),
    });
    // A rule layer's shape legend registers ahead of the others on the right edge.
    let shape_leads = rule_shape && legend_edge == facet_compose::LegendEdge::Right;
    let (shape_lead, shape_trail) = if shape_leads { (shape_chrome, None) } else { (None, shape_chrome) };
    let chrome = facet_compose::FacetChrome {
        title: title_plan.as_ref().map(|plan| facet_compose::TitleChrome {
            ink: plan.ink.clone(),
            paint: Box::new(move |svg, x, y| plan.paint(&r.title_source(), svg, x, y)),
        }),
        legends: shape_lead.into_iter().chain(vega_legend.as_ref().map(|vl| facet_compose::LegendChrome {
            edge: legend_edge,
            width: Box::new(|_, _| vl.box_w),
            height: Box::new(|_| vl.box_h()),
            paint: Box::new(|svg, x, y, _, _| {
                if layered {
                    legend::emit_vega_legend(svg, &layered_entries, vl, legend_cfg, layered_legend.base_glyph, x, y, false)
                } else {
                    legend::emit_vega_legend(svg, &legend_entries, vl, legend_cfg, default_symbol, x, y, false)
                }
            }),
        }))
        .chain(gradient_color.as_ref().map(|(scale, title)| facet_compose::LegendChrome {
            edge: legend_edge,
            width: Box::new(|pw, ph| {
                GradientAt::measure(legend_cfg, r.color_format.as_ref(), scale, (&gradient_id, title), fonts, (pw, ph))
                    .expect("legend fonts are measured when the numeric legends are built")
                    .0
            }),
            height: Box::new(|ph| {
                GradientAt::measure(legend_cfg, r.color_format.as_ref(), scale, (&gradient_id, title), fonts, (0.0, ph))
                    .expect("legend fonts are measured when the numeric legends are built")
                    .1
            }),
            paint: Box::new(|svg, x, y, pw, ph| {
                GradientAt::paint(legend_cfg, r.color_format.as_ref(), scale, (&gradient_id, title), fonts, (pw, ph), (svg, x, y))
                    .expect("legend fonts are measured when the numeric legends are built");
            }),
        }))
        .chain((0..numeric_legends.len()).filter(|_| r.legend.visible).map(|index| {
            let numeric = &numeric_legends;
            facet_compose::LegendChrome {
                edge: legend_edge,
                width: Box::new(move |pw, ph| {
                    numeric.measure_part(index, legend_cfg, fonts, (pw, ph)).expect("legend fonts are measured when the numeric legends are built").0
                }),
                height: Box::new(move |ph| {
                    numeric.measure_part(index, legend_cfg, fonts, (0.0, ph)).expect("legend fonts are measured when the numeric legends are built").1
                }),
                paint: Box::new(move |svg, x, y, pw, ph| {
                    numeric.paint_part(index, svg, legend_cfg, fonts, (pw, ph), (x, y)).expect("legend fonts are measured when the numeric legends are built");
                }),
            }
        }))
        .chain(size_legend.as_ref().map(|sl| facet_compose::LegendChrome {
            edge: legend_edge,
            width: Box::new(|_, _| sl.w),
            height: Box::new(|_| sl.h),
            paint: Box::new(|svg, x, y, _, _| svg.push_str(&print::print(&size_legend_nodes(sl, r, x, y)))),
        }))
        .chain(shape_trail)
        .collect(),
    };
    let spec = facet_compose::FacetSpec {
        family: "scatter",
        panels: &panels,
        axes,
        panel_rows_n,
        panel_cols_n,
        row_axis_idx: index_of(&mc.rows),
        col_axis_idx: index_of(&mc.columns),
        has_mirror,
        left_axis_only: false,
        left_title: None,
        mirror_in_panel: false,
        own_value_axes: embedded_y,
        y_axes_in_cells: embedded_y,
        extra_axis_px,
        cell_x_axis: false,
        axis_x: &r.style.axis_x,
        axis_y: &r.style.axis_y,
        header_font_family: &header_font_family,
        background: r.background.as_str(),
        x_scale: if shape.x_nominal { crate::ViewScale::Discrete } else { crate::ViewScale::Continuous },
    };
    // A shared left value axis is the composite's `row_header`: the first panel's ladder, once per row.
    let y_title_paint = crate::common::wrapped_axis_title(
        &r.id,
        "y_label",
        &base_y_title,
        &r.style.axis_y.title.font,
        title_boxes.1 .0,
        title_boxes.1 .1,
        fonts,
    )?;
    let row_axis = if left_axis && !embedded_y {
        Some(facet_compose::RowAxisSpec {
            title: &y_title_paint,
            title_style: &r.style.axis_y.title,
            reserve: 0.0,
            label_x: 0.0,
            label_anchor: "end",
            title_x: 0.0,
            hugs_labels: Some(crate::common::y_title_extra(&r.style.axis_y.title, &y_title_paint)),
        })
    } else {
        None
    };
    facet_compose::compose_facet_full(payload, fonts, &spec, Some(&chrome), row_axis.as_ref(), &render_pass)
}

/// One facet panel's overrides of the single-chart fit arm. `payload.rows`
/// carries the panel's own rows; every shared domain (the baseline-rule
/// extents, the color domain) is read off `shared_rows`, the whole chart's, so
/// all panels agree (`multiples.scale: "shared"`). The color domain keeps only
/// the rows `kept_reading` keeps; the rule extents scan them all.
pub(crate) struct ScatterPanelOpts<'a> {
    /// This panel's canonical key, the `panel` an annotation names.
    pub(crate) panel_key: &'a str,
    /// This panel's place in `multiples::regroup` order; keeps clip ids unique.
    pub(crate) panel_ordinal: usize,
    pub(crate) plot_w: f64,
    pub(crate) plot_h: f64,
    pub(crate) shared_rows: &'a [serde_json::Map<String, Value>],
    /// The x axis, resolved once against the composite's per-panel crowding
    /// width and shared by every panel.
    pub(crate) x_axis: FacetX<'a>,
    /// Only the last column of a row draws the shared value axis; every
    /// other panel reserves its gutter and paints nothing into it.
    pub(crate) paint_value_axis: bool,
    /// A single-column facet folds the value-axis gutter into its row's ink.
    pub(crate) fold_axis_gutter: bool,
    /// `row_footer` anchors the value axis at `ceil(cell x2)`, past `plot_w`
    /// by the marks' own right reach.
    pub(crate) value_axis_right_overhang: f64,
    /// The left ink margin every panel of the composite shares (`0.0` lets a
    /// panel take its own).
    pub(crate) left_overhang: f64,
    /// `axis_y.mirror`: each panel carries the invisible ghost rule layer the
    /// mirrored right-edge axis rides.
    pub(crate) has_mirror: bool,
    /// `multiples.scale: "independent"`: the panel fits its own y domain and
    /// ladder, and its value axis sits in its cell at the plot edge.
    pub(crate) independent_scale: bool,
    /// The panel fits its own y domain and draws its value axis in its cell,
    /// anchored at the plot edge: an independent scale, or a categorical y
    /// narrowed to the panel's own categories.
    pub(crate) embedded_y: bool,
}

#[cfg(test)]
mod tests {
    use super::{render, LinearX, LogAxis, ScatterResolved};
    use measure::FontBook;
    use crate::Payload;

    const TEST_FONT: &str = "'Inter Variable', Inter, sans-serif";

    #[test]
    fn snap_gives_each_category_its_own_rounded_band() {
        // 30 categories over 250px: unrounded centers are 4.17 + 8.33 i, rounded ones 9 + 8 i;
        // rounded ones 9 + 8 i; category 1 must land on 17, never on category 0's 9.
        let round = super::NominalRound { inner: 1.0, outer: 0.5, n: 30 };
        let centers = super::point_x_fracs(0.5, 30);
        let snapped = round.snap(centers[1], &centers, 250.0);
        assert_eq!(snapped * 250.0, 17.0);
    }

    #[test]
    fn a_rounded_point_scale_floors_its_step_and_rounds_its_start() {
        // d3: step floor(500 / 3) = 166, start round((500 - 332) / 2) = 84.
        let (starts, bandwidth) = super::round_band_positions(500.0, 3, 1.0, 0.5);
        assert_eq!((starts, bandwidth), (vec![84.0, 250.0, 416.0], 0.0));
        // A band scale with paddingInner 0.25: step floor(500 / 3.25) = 153.
        let (starts, bandwidth) = super::round_band_positions(500.0, 3, 0.25, 0.25);
        assert_eq!((starts, bandwidth), (vec![40.0, 193.0, 346.0], 115.0));
    }

    fn test_fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(&dir).expect("test fonts load")
    }

    fn axis_json(position: Option<&str>) -> serde_json::Value {
        serde_json::json!({
            "grid": {"visible": true, "threshold": null, "width": 1.5, "color": "#D0D3D9", "opacity": 1.0},
            "ticks": {"color": "#D0D3D9", "visible": false, "count": 6.0, "length": 6.0, "offset": 1.0},
            "labels": {
                "font": {"family": TEST_FONT, "color": "#626366", "size": 11.0, "weight": "400"},
                "padding": 6.0, "min_gap": null, "format": ".3~s", "visible": true
            },
            "title": {
                "font": {"family": TEST_FONT, "size": 11.0, "weight": "600", "color": "#8A8C8F"},
                "padding": 10.0,
                "visible": false
            },
            "position": position,
            "mirror": null, "band_position": 0.5, "scale": {},
            "tick_values": [], "domain_max": null, "domain_min": null,
            "ruler": null, "tick_label": null,
            "time_unit": null, "fiscal_year_start_month": 1, "fill": null, "line": null,
            "is_quantitative": true, "zero_anchored": false
        })
    }

    /// Fit-shaped scatter payload mirroring the captured corpus specs:
    /// right y axis with a baked ladder + domain, x with an authored
    /// format, quantitative rows.
    fn make_scatter_payload(with_color: bool) -> serde_json::Value {
        let rows = if with_color {
            serde_json::json!([
                {"spend": 18.0, "pipeline": 140.0, "segment": "SMB"},
                {"spend": 27.0, "pipeline": 200.0, "segment": "Mid"},
                {"spend": 39.0, "pipeline": 250.0, "segment": "SMB"},
            ])
        } else {
            serde_json::json!([
                {"spend": 18.0, "pipeline": 140.0},
                {"spend": 27.0, "pipeline": 200.0},
                {"spend": 39.0, "pipeline": 250.0},
            ])
        };
        let mut ay = axis_json(Some("right"));
        ay["labels"]["align"] = serde_json::json!("right");
        ay["tick_values"] = serde_json::json!([150.0, 200.0, 250.0]);
        ay["domain_min"] = serde_json::json!(131.5);
        ay["domain_max"] = serde_json::json!(254.5);
        let resolved = serde_json::json!({
            "chart_type": "scatter", "field_labels": {},
            "id": "c", "title": null, "title_style": null, "background": "#ffffff",
            "x": "spend", "y": "pipeline",
            "color": if with_color { serde_json::json!("segment") } else { serde_json::Value::Null },
            "palette": ["#3164a3", "#779bc9"],
            "multiples": null, "layers": [], "size": null, "shape": null,
            "legend": {
                "visible": with_color, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
                "title": {"font": {"case": "title", "family": TEST_FONT, "size": 12.0, "weight": "600", "color": "#313233"}},
                "label": {"font": {"family": TEST_FONT, "size": 11.0, "weight": "400", "color": "#626366"}, "padding": 8.0}
            },
            "style": {
                "axis_x": axis_json(None),
                "axis_y": ay,
                "point_mark": {"size": 60.0, "opacity": 1.0, "stroke_width": 2.5},
                "single_series_fill": "#23467f"
            }
        });
        serde_json::json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 432.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": {"left": 16.0, "right": 16.0, "top": 16.0, "bottom": 16.0}
        })
    }

    fn render_json(json: serde_json::Value) -> String {
        let payload: Payload = serde_json::from_value(json).unwrap();
        render(&payload, &test_fonts()).expect("scatter renders")
    }

    /// The size legend's markup: from its title text to the end of the document.
    fn size_legend_markup(svg: &str) -> &str {
        let at = svg.rfind(">Deals</text>").expect("a size legend titled Deals");
        &svg[svg[..at].rfind("<text").expect("the title opens a text")..]
    }

    /// Each size-legend variant's payload, by name.
    fn size_legend_variants() -> Vec<(&'static str, serde_json::Value)> {
        let plain = || {
            let mut json = make_scatter_payload_with_size(false);
            json["resolved"]["legend"]["visible"] = serde_json::json!(true);
            json
        };
        let mut out = Vec::new();
        out.push(("colored", make_scatter_payload_with_size(true)));
        out.push(("plain", plain()));
        let mut horizontal = make_scatter_payload_with_size(true);
        horizontal["resolved"]["legend"]["direction"] = serde_json::json!("horizontal");
        out.push(("horizontal", horizontal));
        let mut unfilled = plain();
        unfilled["resolved"]["style"]["point_mark"]["filled"] = serde_json::json!(false);
        unfilled["resolved"]["style"]["point_mark"]["fill"] = serde_json::json!("#ffffff");
        out.push(("unfilled", unfilled));
        let mut unfilled_colored = make_scatter_payload_with_size(true);
        unfilled_colored["resolved"]["style"]["point_mark"]["filled"] = serde_json::json!(false);
        unfilled_colored["resolved"]["style"]["point_mark"]["fill"] = serde_json::json!("#ffffff");
        out.push(("unfilled_colored", unfilled_colored));
        let mut square = plain();
        square["resolved"]["style"]["point_mark"]["shape"] = serde_json::json!("square");
        out.push(("square", square));
        out
    }

    /// The size legend's bytes, recorded before it was built from `legend::SizeLegend`'s nodes:
    /// vertical and horizontal, beside a color legend or alone, filled, unfilled and
    /// another glyph.
    #[test]
    fn size_legend_markup_is_pinned() {
        let mut dump = String::new();
        for (name, json) in size_legend_variants() {
            dump.push_str(&format!("====={name}\n{}\n", size_legend_markup(&render_json(json))));
        }
        assert_eq!(dump, include_str!("scatter_size_legend_golden.txt"));
    }

    /// The plot group's translate and a titled legend's box, for the
    /// `make_scatter_payload(true)` fixture at `edge` (`direction` as the
    /// resolver gives it).
    fn edge_legend(edge: &str, direction: &str) -> (String, (f64, f64), (f64, f64)) {
        let fonts = test_fonts();
        let mut p = make_scatter_payload(true);
        p["resolved"]["legend"]["position"]["edge"] = serde_json::json!(edge);
        p["resolved"]["legend"]["direction"] = serde_json::json!(direction);
        let svg = render_json(p);
        let group = svg.split("<g transform=\"translate(").nth(1).unwrap().split(')').next().unwrap();
        let (gx, gy) = group.split_once(',').unwrap();
        let label_w = |l: &str| fonts.measure_shaped(l, TEST_FONT, 11.0).unwrap();
        let title_w = fonts.measure_shaped("Segment", TEST_FONT, 12.0).unwrap();
        // A vertical column's width; one 11px row or two 13px-pitched rows
        // under the 17px title band.
        let box_w = (20.0 + label_w("Mid").max(label_w("SMB"))).max(title_w).ceil();
        let box_h = if direction == "vertical" { 17.0 + 24.0 } else { 17.0 + 11.0 };
        (svg.clone(), (gx.parse().unwrap(), gy.parse().unwrap()), (box_w, box_h))
    }

    /// A left legend anchors at `floor(xBounds.x1) - 18 - box_w`
    /// (`legend.js:41-46`); scatter's y axis sits right and its points are
    /// bare symbol marks that reach only the view bounds (`ViewLayout.js:
    /// 62-84`), so `xBounds.x1` is the plot's own 0: the legend ends 18px
    /// short of the plot and is the view's leftmost ink.
    #[test]
    fn a_left_legend_ends_the_offset_short_of_the_plot() {
        let (svg, (gx, gy), (box_w, _)) = edge_legend("left", "vertical");
        let title_x = text_x(&svg, "Segment");
        assert_eq!(title_x, -(18.0 + box_w), "{svg}");
        assert_eq!(gx + title_x, 16.0, "the leftmost ink sits at the padded edge: {svg}");
        let _ = gy;
    }

    /// A top legend sits `18 + box_h` above the plot (`floor(yBounds.y1)`
    /// with no x axis on top); a bottom one `ceil(yBounds.y2) + 18` below
    /// it, `yBounds` the plot rect ∪ the x axis's label band, and is the
    /// view's bottom-most ink.
    #[test]
    fn top_and_bottom_legends_anchor_on_the_x_axis_bounds() {
        let title_top = |svg: &str| {
            let end = svg.find(">Segment</text>").unwrap();
            attr(&svg[svg[..end].rfind("<text").unwrap()..end], "y") - measure::baseline_shift("top", 12.0)
        };
        let (svg, _, (_, box_h)) = edge_legend("top", "horizontal");
        assert_eq!(title_top(&svg), -(18.0 + box_h), "{svg}");
        let (svg, (_, gy), (_, box_h)) = edge_legend("bottom", "horizontal");
        let (_, ph) = plot_size(&svg);
        let top = title_top(&svg);
        assert_eq!(gy + top + box_h, 432.0 - 16.0, "the bottom-most ink sits at the padded edge: {svg}");
        // Past the x axis's tick, padding and 11px label row, not the bare plot edge.
        assert!(top - ph >= 18.0 + 11.0 && top.fract() == 0.0, "legend top {top} over plot height {ph}: {svg}");
    }

    /// A vertical legend over `columns` fills column-major through the
    /// shared law (`legend::entry_columns`): two entries over two columns
    /// share one row.
    #[test]
    fn a_right_legend_over_two_columns_puts_both_entries_on_one_row() {
        let mut p = make_scatter_payload(true);
        p["resolved"]["legend"]["columns"] = serde_json::json!(2);
        let svg = render_json(p);
        let y = |label: &str| {
            let end = svg.find(&format!(">{label}</text>")).unwrap();
            attr(&svg[svg[..end].rfind("<text").unwrap()..end], "y")
        };
        assert_eq!(y("Mid"), y("SMB"), "{svg}");
        assert!(text_x(&svg, "SMB") > text_x(&svg, "Mid") + 20.0, "{svg}");
    }

    /// Vega symbol size is the AREA of the bounding square: the drawn circle
    /// radius is √size/2 (oracle: size-60 data marks are d="M3.873,0A…",
    /// size-100 legend symbols r=5).
    #[test]
    fn symbol_radius_is_sqrt_size_over_two() {
        let svg = render_json(make_scatter_payload(false));
        assert_eq!(svg.matches("M3.873,0A3.873").count(), 3, "one per datum: {svg}");
    }

    /// A scatter with no color channel paints every point production's
    /// `single_series_fill` (`emitters/scatter.py`'s `emit_scatter_layer`),
    /// never `palette[0]` — the two coincide on the neon/vivid themes and
    /// differ on clarity, which is why the payload here pins a distinct
    /// value from `palette[0]`.
    #[test]
    fn no_color_channel_paints_single_series_fill_not_palette_zero() {
        let svg = render_json(make_scatter_payload(false));
        assert_eq!(svg.matches("fill=\"#23467f\"").count(), 3, "all points: {svg}");
        assert!(!svg.contains("fill=\"#3164a3\""), "never palette[0]: {svg}");
    }

    /// Same law as `bar.rs`'s canvas-rect test — see
    /// `ScatterResolved::background`.
    #[test]
    fn canvas_rect_reads_the_chart_own_background_not_the_board_background() {
        let mut json = make_scatter_payload(false);
        json["resolved"]["background"] = serde_json::json!("#d9a45b");
        json["background"] = serde_json::json!("#ffffff");
        let svg = render_json(json);
        assert!(svg.contains("<rect width=\"600.0\" height=\"432.0\" fill=\"#d9a45b\"/>"), "{svg}");
    }

    /// Same law on the LEGACY arm (a left y axis routes here instead).
    #[test]
    fn fit_arm_no_color_channel_paints_single_series_fill() {
        let mut p = make_scatter_payload(false);
        p["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
        let svg = render_json(p);
        assert_eq!(svg.matches("fill=\"#23467f\"").count(), 3, "all points: {svg}");
        assert!(!svg.contains("fill=\"#3164a3\""), "never palette[0]: {svg}");
    }

    fn fit_plot_top(svg: &str) -> f64 {
        let i = svg.find("<g transform=\"translate(").expect("plot group");
        svg[i..].split(',').nth(1).unwrap().split(')').next().unwrap().trim().parse().unwrap()
    }

    fn fit_plot_left(svg: &str) -> f64 {
        let i = svg.find("<g transform=\"translate(").expect("plot group");
        svg[i..]
            .split("translate(")
            .nth(1)
            .unwrap()
            .split(',')
            .next()
            .unwrap()
            .parse()
            .unwrap()
    }

    /// `render_legacy` reserves and paints a subtitle via `wrap_title_block`/
    /// `emit_title_block_vega`, the Title-block law
    /// (`docs/laws-shared.md#title-block`).
    #[test]
    fn fit_arm_draws_and_reserves_the_subtitle() {
        let build = |subtitle: Option<&str>| {
            let mut p = make_scatter_payload(false);
            p["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
            p["resolved"]["title"] = serde_json::json!("Spend vs. pipeline");
            p["resolved"]["title_style"] = serde_json::json!({
                "font": {"family": TEST_FONT, "size": 18.0, "weight": 500.0, "case": "none"},
                "position": {"offset": 10.0},
                "subtitle": {"font": {"family": TEST_FONT, "size": 13.0, "weight": "400"}}
            });
            if let Some(t) = subtitle {
                p["resolved"]["subtitle"] = serde_json::json!(t);
            }
            p
        };
        let with_sub = render_json(build(Some("Q3 pipeline")));
        let without = render_json(build(None));

        assert!(with_sub.contains(">Q3 pipeline<"), "the subtitle paints: {with_sub}");

        let delta = fit_plot_top(&with_sub) - fit_plot_top(&without);
        let want = 13.0 + crate::constants::layout::VEGA_SUBTITLE_PADDING;
        assert_eq!(delta, want, "the subtitle band is reserved: plot moved {delta}, expected {want}");
    }

    /// A `wrap-two` subtitle (the exact `p1_scatter_y_only` text) wraps to
    /// two lines and reserves `TitleInk::with_subtitle_lines`' own `ink_h`,
    /// not a single line's.
    #[test]
    fn fit_arm_wraps_and_reserves_a_two_line_subtitle() {
        let long_sub = "EXPECT: heavy rule at y=0. NO rule on x: data runs 12-67, never 0; \
                         the 0 tick you see is Vega-Lite rounding the axis, not a real domain \
                         edge.";
        let build = |subtitle: Option<&str>| {
            let mut p = make_scatter_payload(false);
            p["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
            p["resolved"]["title"] = serde_json::json!("1 \u{b7} Scatter, y crosses 0 only");
            p["resolved"]["title_style"] = serde_json::json!({
                "font": {"family": TEST_FONT, "size": 18.0, "weight": 500.0, "case": "none"},
                "position": {"offset": 10.0},
                "subtitle": {"font": {"family": TEST_FONT, "size": 11.0, "weight": "400"}}
            });
            if let Some(t) = subtitle {
                p["resolved"]["subtitle"] = serde_json::json!(t);
            }
            p
        };
        let fonts = test_fonts();
        assert!(
            fonts.measure(long_sub, TEST_FONT, 11.0).unwrap() > 600.0 - 16.0 - 16.0,
            "the subtitle must outrun avail_w for this test to exercise the wrap"
        );

        let one_line = render_json(build(Some("Q3 pipeline")));
        let wrapped = render_json(build(Some(long_sub)));

        assert!(
            wrapped.contains(">EXPECT: heavy rule at y=0.") && wrapped.contains("domain edge."),
            "both wrapped lines paint: {wrapped}"
        );
        assert_eq!(
            wrapped.matches("data-authored-kind=\"subtitle\"").count(),
            1,
            "only the FIRST subtitle line stamps the authored-kind marker \
             (one `role-title-subtitle` group for both lines, matching the \
             title's own one-stamp-per-wrap law): {wrapped}"
        );
        let delta = fit_plot_top(&wrapped) - fit_plot_top(&one_line);
        assert_eq!(
            delta,
            11.0 + crate::vega_fit::TitleInk::LINE_ADVANCE_EXTRA,
            "the second subtitle line deepens the legacy top margin: {wrapped}"
        );
    }

    /// A `wrap-two` TITLE (not just a subtitle) also wraps in this arm, so
    /// paint and `wrap_title_block`'s own reservation must count the same
    /// lines: both title lines paint, and the subtitle sits below the
    /// SECOND line.
    #[test]
    fn fit_arm_wraps_a_long_title_and_paints_the_subtitle_under_both_lines() {
        let long_title = "A genuinely long chart title that overflows the available card \
                           width entirely, on its own, with no help from any subtitle";
        let mut p = make_scatter_payload(false);
        p["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
        p["resolved"]["title"] = serde_json::json!(long_title);
        p["resolved"]["title_style"] = serde_json::json!({
            "font": {"family": TEST_FONT, "size": 18.0, "weight": 500.0, "case": "none"},
            "position": {"offset": 10.0},
            "subtitle": {"font": {"family": TEST_FONT, "size": 11.0, "weight": "400"}}
        });
        p["resolved"]["subtitle"] = serde_json::json!("Q3 pipeline");
        let fonts = test_fonts();
        assert!(
            fonts.measure(long_title, TEST_FONT, 18.0).unwrap() > 600.0 - 16.0 - 16.0,
            "the title must outrun avail_w for this test to exercise the wrap"
        );

        let svg = render_json(p);
        let title_lines: Vec<&str> =
            svg.split("<text ").skip(1).filter(|t| t.contains("font-size=\"18\"")).collect();
        assert_eq!(title_lines.len(), 2, "both wrapped title lines paint: {svg}");

        let sub_tag = svg
            .split("<text ")
            .skip(1)
            .find(|t| t.contains(">Q3 pipeline<"))
            .unwrap_or_else(|| panic!("the subtitle paints: {svg}"));
        assert!(
            attr(sub_tag, "y") > attr(title_lines[1], "y"),
            "the subtitle sits below the SECOND title line: {svg}"
        );
    }

    /// A `wrap-two` title with NO subtitle takes the same
    /// `wrap_title_block`/`emit_title_block_vega` path: both wrapped lines
    /// paint, one `fontSize + 2` apart, and the plot top moves by exactly
    /// the second line's advance over the one-line title (production's
    /// title block reserves `offset + ink_h` whatever the line count,
    /// the Title wrap law in `docs/laws-shared.md`).
    #[test]
    fn fit_arm_wraps_and_reserves_a_long_title_with_no_subtitle() {
        let long_title = "A genuinely long chart title that overflows the available card \
                           width entirely, on its own, with no help from any subtitle";
        let build = |title: &str| {
            let mut p = make_scatter_payload(false);
            p["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
            p["resolved"]["title"] = serde_json::json!(title);
            p["resolved"]["title_style"] = serde_json::json!({
                "font": {"family": TEST_FONT, "size": 18.0, "weight": 500.0, "case": "none"},
                "position": {"offset": 10.0}
            });
            p
        };
        let fonts = test_fonts();
        assert!(
            fonts.measure(long_title, TEST_FONT, 18.0).unwrap() > 600.0 - 16.0 - 16.0,
            "the title must outrun avail_w for this test to exercise the wrap"
        );

        let one_line = render_json(build("Spend vs. pipeline"));
        let wrapped = render_json(build(long_title));

        let title_lines: Vec<&str> =
            wrapped.split("<text ").skip(1).filter(|t| t.contains("font-size=\"18\"")).collect();
        assert_eq!(title_lines.len(), 2, "both wrapped title lines paint: {wrapped}");
        let advance = 18.0 + crate::vega_fit::TitleInk::LINE_ADVANCE_EXTRA;
        assert_eq!(
            attr(title_lines[1], "y") - attr(title_lines[0], "y"),
            advance,
            "the second line sits one fontSize + 2 below the first: {wrapped}"
        );
        assert_eq!(
            fit_plot_top(&wrapped) - fit_plot_top(&one_line),
            advance,
            "the reserved band counts the second line: {wrapped}"
        );
    }

    /// The left-axis branch composes its margin as Vega lays the axis out:
    /// `ceil(label_w + label_pad + y_title_extra)`, the ceil taken over the
    /// whole left ink (`vega_fit.rs` `left = (-b.x1).ceil()`), with the
    /// rotated title's reserve (`axisTitleLayout`: padding + round(0.21 fs) +
    /// round(0.8 fs)) present only while the title is visible. Probed
    /// (`negative-unbaked-axes`, reference origin 44 hidden / 65 visible).
    #[test]
    fn fit_arm_left_axis_margin_is_the_ceil_of_label_pad_and_title_reserve() {
        let rows = [(10.0, 100.0), (20.0, 180.0), (30.0, 220.0), (40.0, 300.0)];
        let fonts = test_fonts();
        let label_w = ["0", "50", "100", "150", "200", "250", "300"]
            .iter()
            .map(|s| fonts.measure(s, TEST_FONT, 11.0).unwrap())
            .fold(0.0_f64, f64::max);
        for (title_visible, title_reserve) in [(false, 0.0), (true, 21.0)] {
            let mut json = fit_unbaked_payload(&rows);
            json["resolved"]["style"]["axis_y"]["title"]["visible"] =
                serde_json::json!(title_visible);
            let svg = render_json(json);
            let expected = 16.0 + (label_w + 6.0 + title_reserve).ceil();
            assert_eq!(
                fit_plot_left(&svg),
                expected,
                "title_visible={title_visible}, label_w={label_w}: {svg}"
            );
        }
    }

    /// Vega's view bounds union every point expanded by its radius
    /// (`boundStroke` of a symbol reaches `sqrt(size) / 2` past its scale
    /// position), so the highest point sitting on the top edge reserves
    /// `ceil(radius)`: probed in `tests/test_vl_probe.py::
    /// test_a_point_on_the_top_edge_reserves_the_ceil_of_its_radius`.
    #[test]
    fn fit_arm_top_margin_reserves_the_ceil_of_a_point_on_the_top_edge() {
        let build = |size: f64, top_row: f64| {
            let mut json = fit_unbaked_payload(&[(10.0, 100.0), (20.0, 200.0), (30.0, top_row)]);
            json["resolved"]["style"]["axis_y"]["tick_values"] = serde_json::json!([100.0, 200.0]);
            json["resolved"]["style"]["axis_y"]["scale"] =
                serde_json::json!({"continuous": {"domain": [0.0, 300.0]}});
            json["resolved"]["style"]["point_mark"]["size"] = serde_json::json!(size);
            fit_plot_top(&render_json(json))
        };
        assert_eq!(build(60.0, 300.0), 16.0 + 4.0, "ceil(sqrt(60) / 2 = 3.87)");
        assert_eq!(build(200.0, 300.0), 16.0 + 8.0, "ceil(sqrt(200) / 2 = 7.07)");
        assert_eq!(build(60.0, 250.0), 16.0, "a point below the edge reserves nothing");
    }

    /// The legacy arm must paint the SAME axis title text `render_fit` and
    /// production do — `common::axis_title_text` (lowercase, tokenized),
    /// never a second `slug_to_display(field, case="title")` reading that
    /// humanizes the field name into Title Case.
    #[test]
    fn fit_arm_axis_title_text_matches_common_axis_title_text_not_slug_to_display() {
        let mut p = make_scatter_payload(false);
        p["resolved"]["style"]["axis_x"]["scale"] =
            serde_json::json!({"continuous": {"domain": [0.0, 100.0]}});
        p["resolved"]["style"]["axis_x"]["title"]["visible"] = serde_json::json!(true);
        p["resolved"]["style"]["axis_y"]["title"]["visible"] = serde_json::json!(true);
        let svg = render_json(p);
        assert!(svg.contains(">Spend<"), "x title reads the sentence-case 'Spend': {svg}");
        assert!(svg.contains(">Pipeline<"), "y title reads the sentence-case 'Pipeline': {svg}");
    }

    /// Front-end contract: `tooltip_format` present wraps every point in a
    /// `.role-mark` group and stamps its structured aria-label
    /// (`charts/point-charts_3/experiment_scatter`'s own shape, see
    /// `contract.rs`'s scatter tests for the formula). No `tooltip_format`
    /// keeps today's unwrapped, unlabeled points — same gate as bar/pie.
    #[test]
    fn tooltip_format_wraps_every_point_with_a_structured_aria_label() {
        let mut p = make_scatter_payload(true);
        p["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.2~f");
        let svg = render_json(p);
        assert!(
            svg.contains("<g class=\"mark-symbol role-mark marks\""),
            "every point wraps in a role-mark group: {svg}"
        );
        assert!(
            svg.contains("aria-label=\"⁤SMB; Pipeline: 140; Spend: 18\""),
            "{svg}"
        );

        let plain = render_json(make_scatter_payload(true));
        assert!(!plain.contains("role-mark"));
        assert!(crate::contract::has_no_mark_labels(&plain));
    }

    /// Every labeled point is a `point` item, and the baseline rules are
    /// `rule mark` -- `chart_interactivity.js` tells a hover target from a
    /// visible mark by the `point` role, and a rule never recedes.
    #[test]
    fn labeled_points_carry_the_point_role() {
        let mut p = make_scatter_payload(true);
        p["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.2~f");
        let svg = render_json(p);
        let points = svg.matches("<g class=\"mark-symbol role-mark marks\"").count();
        assert!(points > 1, "{svg}");
        assert_eq!(svg.matches("aria-roledescription=\"point\"").count(), points, "{svg}");
    }

    /// A dot-plot y row carries its own tooltip, title included (a real
    /// `_row_expr`, not a bare header/series role) — `charts/point-
    /// charts_2/team_response_dots`'s own shape.
    #[test]
    fn tooltip_format_labels_a_dot_plot_by_its_own_row() {
        let mut p = make_dot_plot_payload(dot_rows("Charlie"));
        p["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.2~f");
        let svg = render_json(p);
        assert!(svg.contains("aria-label=\"Team: Bravo; Hours: 3\""), "{svg}");
    }

    /// `_series_order_role`: a colored dot plot whose color scale carries an
    /// explicit domain (the board's `category_colors`) adds a `ROLE_ORDER`
    /// rank row — the series' index in the sorted domain — to its tooltip,
    /// ahead of the measure row. The row lives only in the tooltip, so the
    /// same chart with no tooltip draws no labels at all.
    #[test]
    fn a_dot_plot_with_a_board_color_scale_ranks_its_series_in_the_tooltip() {
        let colored = |tooltip_format: &str| {
            let mut p = make_dot_plot_payload(serde_json::json!([
                {"median_response_hours": 3.0, "team": "Bravo", "segment": "SMB"},
                {"median_response_hours": 1.0, "team": "Alpha", "segment": "Mid"},
            ]));
            p["resolved"]["color"] = serde_json::json!("segment");
            p["resolved"]["category_colors"] =
                serde_json::json!([{"field": "segment", "slots": {"SMB": 0, "Mid": 1}, "overrides": {}}]);
            p["resolved"]["style"]["tooltip_format"] = serde_json::json!(tooltip_format);
            p
        };
        let svg = render_json(colored(",.2~f"));
        assert!(svg.contains("\u{2061}Bravo; \u{2062}SMB; \u{200c}1; Hours: 3"), "{svg}");
        assert!(svg.contains("\u{2061}Alpha; \u{2062}Mid; \u{200c}0; Hours: 1"), "{svg}");
        let svg = render_json(colored(""));
        assert!(crate::contract::has_no_mark_labels(&svg), "{svg}");
    }

    /// `_value_tooltip_field`'s own type inference would call an
    /// all-date-like dot-plot y "temporal" and reformat it — this port has
    /// no ported detection for that, so it skips the tooltip wiring
    /// silently (same posture as line/area's own unmodeled shapes) rather
    /// than refusing the whole chart over a shape that renders correctly
    /// today. No corpus row hits this.
    #[test]
    fn a_date_like_dot_plot_y_skips_the_tooltip_rather_than_mislabel() {
        let mut p = make_dot_plot_payload(serde_json::json!([
            {"median_response_hours": 3.0, "team": "2024-01-01"},
            {"median_response_hours": 1.0, "team": "2024-02-01"},
        ]));
        p["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.2~f");
        let payload: Payload = serde_json::from_value(p).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders despite the unmodeled date-like y");
        assert!(!svg.contains("role-mark"), "no tooltip wiring for a date-like y: {svg}");
    }

    /// A date x typed ordinal over 130 consecutive years: the ladder steps the labels to multiples of 5 or 10,
    /// where it used to draw every one.
    #[test]
    fn a_dense_ordinal_year_x_labels_only_the_stepped_years() {
        let mut p = make_scatter_payload(false);
        p["rows"] = serde_json::Value::Array(
            (0..130).map(|i| serde_json::json!({"spend": format!("{}-01-01", 1900 + i), "pipeline": 150.0 + i as f64})).collect(),
        );
        let ax = &mut p["resolved"]["style"]["axis_x"];
        ax["type"] = serde_json::json!("ordinal");
        ax["is_quantitative"] = serde_json::json!(false);
        ax["labels"]["format"] = serde_json::json!(null);
        ax["labels"]["overlap"] = serde_json::json!({"skip": true, "tilt": true});
        ax["labels"]["tilt_increments"] = serde_json::json!([0.0, -30.0, -45.0, -60.0, -90.0]);
        p["resolved"]["style"]["label_usable_ratio"] = serde_json::json!(0.8);
        let payload: Payload = serde_json::from_value(p).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let years: Vec<i32> = svg
            .split('>')
            .filter_map(|chunk| chunk.split('<').next())
            .filter(|t| t.len() == 4 && t.bytes().all(|b| b.is_ascii_digit()))
            .map(|t| t.parse().unwrap())
            .collect();
        assert!(!years.is_empty() && years.len() < 40 && years.iter().all(|y| y % 5 == 0), "{years:?}");
    }

    /// A payload lacking `style.single_series_fill` refuses rather than
    /// falling back to the palette.
    #[test]
    fn missing_single_series_fill_refuses() {
        let mut json = make_scatter_payload(false);
        json["resolved"]["style"].as_object_mut().unwrap().remove("single_series_fill");
        let payload: Payload = serde_json::from_value(json).unwrap();
        assert!(render(&payload, &test_fonts()).is_err(), "missing field refuses");
    }

    /// A numeric color column painted on `rows` (`spend`, `pipeline`, `score`).
    fn quantitative_color_payload(scores: &[serde_json::Value]) -> serde_json::Value {
        let mut json = make_scatter_payload(true);
        json["rows"] = serde_json::Value::Array(
            scores
                .iter()
                .enumerate()
                .map(|(i, score)| {
                    serde_json::json!({"spend": 10.0 + 5.0 * i as f64, "pipeline": 150.0 + 10.0 * i as f64, "score": score})
                })
                .collect(),
        );
        json["resolved"]["color"] = serde_json::json!("score");
        json["resolved"]["resolved_channels"] = serde_json::json!({
            "color": {"mode": "series", "data_field": "score", "quantitative_data": true}
        });
        json
    }

    /// Oracle: vega 5.25 `linear` scale over `[3, 10]` with Vega-Lite's default
    /// `blues` ramp, and its gradient legend (vertical, 200px, labels at
    /// `ticks(4)`).
    #[test]
    fn a_quantitative_color_is_the_blues_ramp_with_a_gradient_legend() {
        let scores: Vec<_> = [3.0, 4.0, 6.5, 10.0].iter().map(|v| serde_json::json!(v)).collect();
        let svg = render_json(quantitative_color_payload(&scores));
        for fill in ["rgb(207, 225, 242)", "rgb(181, 212, 233)", "rgb(91, 163, 207)", "rgb(10, 74, 144)"] {
            assert!(svg.contains(&format!("fill=\"{fill}\" stroke-width")), "{fill} missing: {svg}");
        }
        assert!(svg.contains("<linearGradient"), "{svg}");
        assert!(svg.contains("stop-color=\"rgb(195, 219, 238)\""), "the 3.5 stop: {svg}");
        let labels = legend_labels(&svg, &["4", "6", "8", "10", "Score"]);
        let title_at = labels.iter().position(|l| l == "Score").expect("legend title");
        assert_eq!(labels[title_at..], ["Score", "4", "6", "8", "10"]);
    }

    fn gradient_legend_payload(position: serde_json::Value) -> String {
        let scores: Vec<_> = [3.0, 4.0, 6.5, 10.0].iter().map(|v| serde_json::json!(v)).collect();
        let mut json = quantitative_color_payload(&scores);
        json["resolved"]["legend"]["position"] = position;
        render_json(json)
    }

    /// A reserved strip's centered gradient legend slides along the right edge
    /// the way `legend_align.py` shifts it, by `(plot_h - block) / 2` from the
    /// start-aligned origin. (`end` on a reserved strip is refused upstream.)
    #[test]
    fn a_gradient_legend_slides_along_the_right_edge() {
        let at = |align: &str| {
            text_xy(&gradient_legend_payload(serde_json::json!({"edge": "right", "align": align, "overlay": false})), "Score")
        };
        let (start, center) = (at("start"), at("center"));
        assert_eq!(center.0, start.0, "sliding keeps the column");
        assert!(center.1 > start.1, "{start:?} {center:?}");
    }

    /// An overlay legend floats in its corner and reserves no strip: the plot
    /// keeps the width the strip would have taken.
    #[test]
    fn a_floating_gradient_legend_reserves_no_strip() {
        let reserved = gradient_legend_payload(serde_json::json!({"edge": "right", "align": "center", "overlay": false}));
        let floating = gradient_legend_payload(serde_json::json!({"edge": "right", "align": "center", "overlay": true}));
        let plot_right = |svg: &str| {
            svg.split("<line ")
                .skip(1)
                .filter_map(|el| {
                    let at = el.find(" x2=\"")? + 5;
                    el[at..at + el[at..].find('"')?].parse::<f64>().ok()
                })
                .fold(0.0, f64::max)
        };
        assert!(plot_right(&floating) > plot_right(&reserved) + 20.0, "the strip is not reserved");
        let title = text_xy(&floating, "Score");
        assert!(title.0 < plot_right(&floating), "the legend sits inside the plot: {title:?}");
    }

    fn with_board_font(json: &mut serde_json::Value) {
        json["board_style"] = serde_json::json!({
            "font": TEST_FONT, "text_mark_font": {"family": TEST_FONT, "size": 11.0}, "secondary_color": null, "tones": null,
            "title_case": null, "continuous_height": null,
            "format_locale": crate::number_locale::en_us_wire(), "time_locale": crate::time_locale::en_us_wire()
        });
    }

    /// A shape on a numeric color's field draws its own legend beside the
    /// gradient: nothing merges a glyph into a color ramp.
    #[test]
    fn a_shape_on_a_numeric_color_keeps_its_shape_legend() {
        let scores = [serde_json::json!(1.0), serde_json::json!(2.0), serde_json::json!(4.0)];
        let mut json = quantitative_color_payload(&scores);
        json["resolved"]["shape"] = serde_json::json!("score");
        with_board_font(&mut json);
        let svg = render_json(json);
        assert!(svg.contains(">score<"), "the shape legend's own lowercase title: {svg}");
        assert_eq!(svg.matches("<linearGradient").count(), 1, "{svg}");
    }

    /// A null color drops its row the way Vega-Lite's invalid filter does, and a
    /// constant color is a one-value domain that still draws.
    #[test]
    fn a_null_color_drops_its_mark_and_a_constant_color_still_draws() {
        let marks = |svg: &str| svg.matches("fill=\"rgb(").count();
        let dropped = render_json(quantitative_color_payload(&[serde_json::json!(1.0), serde_json::json!(null)]));
        assert_eq!(marks(&dropped), 1, "{dropped}");
        let constant = render_json(quantitative_color_payload(&[serde_json::json!(5.0), serde_json::json!(5.0)]));
        assert_eq!(marks(&constant), 2, "{constant}");
    }

    /// An authored `legend.values` replaces the gradient's ladder; a token that
    /// is no number is labeled `NaN`.
    #[test]
    fn authored_legend_values_label_the_gradient() {
        let scores = [serde_json::json!(1.0), serde_json::json!(2.0), serde_json::json!(9.0)];
        let mut json = quantitative_color_payload(&scores);
        json["resolved"]["legend"]["values"] = serde_json::json!(["2", "5"]);
        let svg = render_json(json);
        let legend = &svg[svg.find(">Score<").expect("the gradient legend's title")..];
        assert_eq!(legend_labels(legend, &["1", "2", "3", "4", "5", "6", "7", "8", "9"]), ["2", "5"], "{svg}");
        let mut json = quantitative_color_payload(&scores);
        json["resolved"]["legend"]["values"] = serde_json::json!(["abc"]);
        let svg = render_json(json);
        let legend = &svg[svg.find(">Score<").expect("the gradient legend's title")..];
        assert_eq!(legend_labels(legend, &["NaN"]), ["NaN"], "{svg}");
    }

    /// The `<text>` bodies equal to one of `names`, in paint order.
    fn legend_labels(svg: &str, names: &[&str]) -> Vec<String> {
        svg.split("<text ")
            .skip(1)
            .filter_map(|el| Some(&el[el.find('>')? + 1..el.find("</text>")?]))
            .filter(|body| names.contains(body))
            .map(String::from)
            .collect()
    }

    fn probe_payload(rows: serde_json::Value) -> serde_json::Value {
        let mut json = make_scatter_payload(true);
        json["rows"] = rows;
        json["resolved"]["palette"] = serde_json::json!(["#3164a3", "#779bc9", "#608470"]);
        json
    }

    /// Vega-Lite's `mark.invalid: "filter"` (`mark.ts:351`,
    /// `normalizeInvalidDataMode.ts:9-10`) puts `FilterInvalidNode`
    /// (`filterinvalid.ts:36-55,85-99`) on the unit's data ahead of every
    /// scale, so a series only on a null-`x` row owns no legend entry.
    #[test]
    fn a_series_only_on_a_null_coordinate_row_has_no_legend_entry() {
        let svg = render_json(probe_payload(serde_json::json!([
            {"spend": 10.0, "pipeline": 100.0, "segment": "A"},
            {"spend": 20.0, "pipeline": 180.0, "segment": "B"},
            {"spend": 30.0, "pipeline": 220.0, "segment": "A"},
            {"spend": null, "pipeline": 300.0, "segment": "Z"},
        ])));
        assert_eq!(legend_labels(&svg, &["A", "B", "Z"]), ["A", "B"]);
    }

    /// The dropped series spends no palette slot: B keeps the second one even
    /// though the dropped series sorts ahead of it.
    #[test]
    fn a_dropped_series_spends_no_palette_slot() {
        let svg = render_json(probe_payload(serde_json::json!([
            {"spend": 10.0, "pipeline": 100.0, "segment": "B"},
            {"spend": 20.0, "pipeline": 180.0, "segment": "C"},
            {"spend": null, "pipeline": 300.0, "segment": "A"},
        ])));
        assert_eq!(legend_labels(&svg, &["A", "B", "C"]), ["B", "C"]);
        assert!(svg.contains("#3164a3") && svg.contains("#779bc9"), "B and C take slots 0 and 1");
        assert!(!svg.contains("#608470"), "no third slot is spent");
    }

    /// A size channel joins the same filter as `x` and `y`.
    #[test]
    fn a_series_only_on_a_null_size_row_has_no_legend_entry() {
        let mut json = probe_payload(serde_json::json!([
            {"spend": 10.0, "pipeline": 100.0, "segment": "A", "w": 5.0},
            {"spend": 20.0, "pipeline": 180.0, "segment": "B", "w": 9.0},
            {"spend": 30.0, "pipeline": 220.0, "segment": "A", "w": 7.0},
            {"spend": 40.0, "pipeline": 300.0, "segment": "Z", "w": null},
        ]));
        json["resolved"]["size"] = serde_json::json!("w");
        let svg = render_json(json);
        assert_eq!(legend_labels(&svg, &["A", "B", "Z"]), ["A", "B"]);
    }

    /// The filter reads quantitative and temporal fields only
    /// (`filterinvalid.ts:36-55`): a null category survives it and Vega draws
    /// its own `null` band, sorted ahead of the others, whose label and
    /// tooltip read `null`.
    #[test]
    fn a_null_category_keeps_its_row_under_a_null_band() {
        let svg = render_json(probe_payload(serde_json::json!([
            {"spend": "Alpha", "pipeline": 100.0, "segment": "A"},
            {"spend": "Bravo", "pipeline": 180.0, "segment": "B"},
            {"spend": null, "pipeline": 300.0, "segment": "Z"},
        ])));
        let labels = svg.find(">null</text>").expect("a null band label");
        assert!(labels < svg.find(">Alpha</text>").unwrap(), "null sorts first: {svg}");
    }

    /// A row whose category AND measure are null is dropped by the measure's
    /// term, so no `null` band forms and its series owns no legend entry.
    #[test]
    fn a_row_null_in_category_and_measure_is_dropped_not_refused() {
        let svg = render_json(probe_payload(serde_json::json!([
            {"spend": "Alpha", "pipeline": 100.0, "segment": "A"},
            {"spend": "Bravo", "pipeline": 180.0, "segment": "B"},
            {"spend": null, "pipeline": null, "segment": "Z"},
        ])));
        assert_eq!(legend_labels(&svg, &["A", "B", "Z"]), ["A", "B"]);
    }

    /// `axis_y.mirror: true` repeats the labels on the opposite edge of either
    /// primary: one copy left of the plot's frame, one right of it.
    #[test]
    fn mirror_repeats_the_labels_on_the_opposite_edge() {
        let mut json = make_scatter_payload(false);
        json["resolved"]["style"]["axis_y"]["mirror"] = serde_json::json!(true);
        for position in ["right", "left"] {
            json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!(position);
            let svg = render_json(json.clone());
            let xs = svg
                .split("<text ")
                .filter(|t| t.contains(">150</text>"))
                .map(|t| {
                    let tag = format!(" {t}");
                    let x = tag.split(" x=\"").nth(1).and_then(|x| x.split('"').next());
                    x.expect("label x").parse::<f64>().expect("numeric x")
                })
                .collect::<Vec<_>>();
            assert_eq!(xs.len(), 2, "{position}: {svg}");
            assert!(xs.iter().any(|x| *x < 0.0) && xs.iter().any(|x| *x > 0.0), "{position}: a label on each edge: {xs:?}");
        }
    }

    /// A descending authored pair reaches Vega verbatim and inverts the axis:
    /// the ladder keeps its order and each rung lands at its inverted position.
    #[test]
    fn descending_authored_y_domain_inverts_the_axis() {
        let y_of = |domain: [f64; 2]| {
            let mut json = make_scatter_payload(false);
            let ay = &mut json["resolved"]["style"]["axis_y"];
            ay["scale"] = serde_json::json!({"continuous": {"domain": domain}});
            ay["tick_values"] = serde_json::json!([0.0, 100.0, 200.0, 300.0, 400.0]);
            ay["domain_min"] = serde_json::Value::Null;
            ay["domain_max"] = serde_json::Value::Null;
            let svg = render_json(json);
            let label = svg.find(">400</text>").expect("400 rung");
            let tail = &svg[..label];
            let y = tail.rfind(" y=\"").expect("label y");
            tail[y + 4..].split('"').next().unwrap().parse::<f64>().unwrap()
        };
        let (ascending, descending) = (y_of([0.0, 400.0]), y_of([400.0, 0.0]));
        assert!(descending > ascending + 50.0, "400 sits at the bottom: {ascending} vs {descending}");
    }

    /// An authored ascending `scale.continuous.domain` reaches Vega verbatim
    /// (`emitters/scatter.py:354-369` via `emit_resolved_scale_vl`), and
    /// resolve leaves `domain_min`/`domain_max` unbaked beside it while the
    /// baked ladder spans the authored pair. The ladder rungs past the data
    /// extent therefore draw.
    #[test]
    fn ascending_authored_y_domain_draws_the_authored_range() {
        let mut json = make_scatter_payload(false);
        let ay = &mut json["resolved"]["style"]["axis_y"];
        ay["scale"] = serde_json::json!({"continuous": {"domain": [0.0, 400.0]}});
        ay["tick_values"] = serde_json::json!([0.0, 100.0, 200.0, 300.0, 400.0]);
        ay["domain_min"] = serde_json::Value::Null;
        ay["domain_max"] = serde_json::Value::Null;
        let svg = render_json(json);
        for rung in ["100", "200", "300", "400"] {
            assert!(svg.contains(&format!(">{rung}</text>")), "rung {rung} missing: {svg}");
        }
    }

    /// A right-axis payload whose resolver baked a tick ladder but no domain
    /// edge (`headroom: 0`): the same payload with the domain authored as
    /// `drawn` is the oracle, because an authored pair reaches Vega verbatim.
    fn unbaked_y_payload(ys: &[f64], ladder: &[f64], zero_false: bool) -> serde_json::Value {
        let mut json = make_scatter_payload(false);
        json["rows"] = serde_json::json!(ys
            .iter()
            .enumerate()
            .map(|(i, y)| serde_json::json!({"spend": 18.0 + 6.0 * i as f64, "pipeline": y}))
            .collect::<Vec<_>>());
        let ay = &mut json["resolved"]["style"]["axis_y"];
        ay["tick_values"] = serde_json::json!(ladder);
        ay["domain_min"] = serde_json::Value::Null;
        ay["domain_max"] = serde_json::Value::Null;
        if zero_false {
            ay["scale"] = serde_json::json!({"continuous": {"zero": false}});
        }
        json
    }

    fn with_authored_y_domain(mut json: serde_json::Value, lo: f64, hi: f64) -> serde_json::Value {
        let ay = &mut json["resolved"]["style"]["axis_y"];
        ay["scale"]["continuous"] = serde_json::json!({"domain": [lo, hi]});
        json
    }

    /// Neither y edge baked: production compiles `{zero: true, nice: true}`
    /// (`emitters/scatter.py:354-358`) and Vega nices the zero-joined extent,
    /// so y -37..43 over a baked ladder -40..40 draws -40..50 and keeps the
    /// -40 rung (probed through `vl_convert`; `fixtures/scatter-specifics/
    /// unbaked-y-domain-crossing-zero`).
    #[test]
    fn an_unbaked_y_domain_crossing_zero_draws_the_niced_extent() {
        let ys = [-37.0, -7.0, 12.0, 25.0, 43.0];
        let ladder = [-40.0, -30.0, -20.0, -10.0, 0.0, 10.0, 20.0, 30.0, 40.0];
        let unbaked = render_json(unbaked_y_payload(&ys, &ladder, false));
        let authored = render_json(with_authored_y_domain(
            unbaked_y_payload(&ys, &ladder, false),
            -40.0,
            50.0,
        ));
        assert!(unbaked.contains(">\u{2212}40</text>"), "the -40 rung is missing");
        assert!(unbaked == authored, "the unbaked domain differs from the authored -40..50");
    }

    /// `zero: false` skips the zero join but keeps `nice`: y 20..43 draws
    /// 20..44 (`fixtures/scatter-specifics/unbaked-y-domain-zero-false`).
    #[test]
    fn an_unbaked_zero_false_y_domain_draws_the_niced_extent() {
        let ys = [20.0, 27.0, 31.0, 38.0, 43.0];
        let ladder = [20.0, 25.0, 30.0, 35.0, 40.0];
        let unbaked = render_json(unbaked_y_payload(&ys, &ladder, true));
        let authored = render_json(with_authored_y_domain(
            unbaked_y_payload(&ys, &ladder, true),
            20.0,
            44.0,
        ));
        assert!(unbaked == authored, "the unbaked domain differs from the authored 20..44");
    }

    /// The fit arm's authored-pair refusal reads the rows Vega keeps: a row
    /// with a null `x` is filtered from the mark, so its `y` outside the pair
    /// refuses nothing.
    #[test]
    fn a_null_x_row_outside_an_authored_y_domain_never_refuses() {
        let mut json = make_scatter_payload(false);
        let ay = &mut json["resolved"]["style"]["axis_y"];
        ay["scale"] = serde_json::json!({"continuous": {"domain": [0.0, 300.0]}});
        ay["tick_values"] = serde_json::json!([0.0, 100.0, 200.0, 300.0]);
        ay["domain_min"] = serde_json::Value::Null;
        ay["domain_max"] = serde_json::Value::Null;
        json["rows"]
            .as_array_mut()
            .unwrap()
            .push(serde_json::json!({"spend": null, "pipeline": 900.0}));
        let svg = render_json(json);
        assert_eq!(symbol_centers(&svg).len(), 3, "{svg}");
    }

    /// A datum outside the authored pair is drawn past the plot edge, unclipped.
    #[test]
    fn a_value_outside_an_authored_y_domain_is_drawn_past_the_plot_edge() {
        let mut json = make_scatter_payload(false);
        let ay = &mut json["resolved"]["style"]["axis_y"];
        ay["scale"] = serde_json::json!({"continuous": {"domain": [0.0, 200.0]}});
        ay["tick_values"] = serde_json::json!([0.0, 100.0, 200.0]);
        ay["domain_min"] = serde_json::Value::Null;
        ay["domain_max"] = serde_json::Value::Null;
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("250 lies outside [0, 200] and renders");
        assert!(svg.contains(">200</text>") && !svg.contains(">250</text>"), "{svg}");
    }

    /// `scale.nice: true` beside an authored pair re-rounds it outward at
    /// count 10 on the fit arm too: `[0, 487]` draws the `[0, 500]` board
    /// byte for byte, top rung included (probed through `vl_convert`,
    /// `fixtures/scatter-specifics/authored-y-domain-nice-left-axis` with the
    /// axis on the right: "values from 0 to 500").
    #[test]
    fn fit_arm_nices_an_authored_y_domain_beside_scale_nice_true() {
        let authored = |hi: f64, nice: bool| {
            let mut json = with_authored_y_domain(make_scatter_payload(false), 0.0, hi);
            let ay = &mut json["resolved"]["style"]["axis_y"];
            ay["tick_values"] = serde_json::json!([0.0, 100.0, 200.0, 300.0, 400.0, 500.0]);
            ay["domain_min"] = serde_json::Value::Null;
            ay["domain_max"] = serde_json::Value::Null;
            if nice {
                ay["scale"]["nice"] = serde_json::json!(true);
            }
            render_json(json)
        };
        let niced = authored(487.0, true);
        assert!(niced.contains(">500</text>"), "the niced top rung is missing: {niced}");
        assert!(niced == authored(500.0, false), "[0, 487] + nice differs from [0, 500]");
    }

    /// `scale.padding` widens an authored domain by pixels of the plot, so the
    /// top rung sits that far inside the plot edge; `scale.round` lands every
    /// position on a whole pixel.
    #[test]
    fn padding_and_round_reshape_an_authored_y_domain() {
        let rung_y = |scale: serde_json::Value, rung: &str| {
            let mut json = make_scatter_payload(false);
            let ay = &mut json["resolved"]["style"]["axis_y"];
            ay["scale"] = scale;
            ay["tick_values"] = serde_json::json!([0.0, 100.0, 200.0, 300.0]);
            ay["domain_min"] = serde_json::Value::Null;
            ay["domain_max"] = serde_json::Value::Null;
            let svg = render_json(json);
            let label = svg.find(&format!(">{rung}</text>")).expect("rung label");
            let tail = &svg[..label];
            let y = tail.rfind(" y=\"").expect("label y");
            tail[y + 4..].split('"').next().unwrap().parse::<f64>().unwrap()
        };
        let domain = serde_json::json!({"continuous": {"domain": [0.0, 300.0]}});
        let plain = rung_y(domain.clone(), "300");
        let padded = rung_y(serde_json::json!({"continuous": {"domain": [0.0, 300.0]}, "padding": 10.0}), "300");
        assert!((padded - plain - 10.0).abs() < 0.5, "10px of padding moves the top rung down: {plain} vs {padded}");
        let rung_gap = |scale: serde_json::Value| rung_y(scale.clone(), "100") - rung_y(scale, "200");
        let (loose, whole) = (
            rung_gap(domain),
            rung_gap(serde_json::json!({"continuous": {"domain": [0.0, 300.0]}, "round": true})),
        );
        assert!((loose - loose.round()).abs() > 1e-6, "an unrounded rung gap is fractional: {loose}");
        assert!((whole - whole.round()).abs() < 1e-6, "scale.round lands the rungs on whole pixels: {whole}");
    }

    /// The board plans `category_colors` once per board (#7167): a value's
    /// slot wins over this chart's own alphabetical position. "SMB" sorts
    /// second locally (would take palette[1]) but owns board slot 0; "Mid"
    /// owns slot 1 — reversed from the local domain. Both data points and
    /// the legend swatch follow the board slot.
    #[test]
    fn board_slot_wins_the_point_fill_and_legend_swatch_over_chart_local_alphabetical_order() {
        let mut p = make_scatter_payload(true);
        p["resolved"]["category_colors"] =
            serde_json::json!([{"field": "segment", "slots": {"SMB": 0, "Mid": 1}, "overrides": {}}]);
        let svg = render_json(p);
        // Data marks in row order: SMB (spend 18), Mid (27), SMB (39).
        let point_fills: Vec<&str> = svg
            .match_indices("M3.873,0A3.873")
            .map(|(i, _)| {
                let tag = svg[i..].split("/>").next().unwrap();
                tag.split("fill=\"").nth(1).unwrap().split('"').next().unwrap()
            })
            .collect();
        assert_eq!(point_fills, ["#3164a3", "#779bc9", "#3164a3"], "{svg}");
        let fill_before_label = |label: &str| -> String {
            let marker = format!(">{label}</text>");
            let end = svg.find(&marker).unwrap_or_else(|| panic!("label {label} present: {svg}"));
            let text_start = svg[..end].rfind("<text").expect("owning <text> tag present");
            let head = &svg[..text_start];
            let fill_idx = head.rfind("fill=\"").expect("swatch fill present");
            head[fill_idx + 6..].split('"').next().unwrap().to_string()
        };
        assert_eq!(fill_before_label("Mid"), "#779bc9", "{svg}");
        assert_eq!(fill_before_label("SMB"), "#3164a3", "{svg}");
    }

    /// Same law on the LEGACY arm (a left y axis routes here instead of the
    /// fit path) — a separate `color_for` closure and legend loop from the
    /// fit arm above, with no coverage of its own before this.
    #[test]
    fn fit_arm_board_slot_wins_the_point_fill_and_legend_swatch() {
        let mut p = make_scatter_payload(true);
        p["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
        p["resolved"]["category_colors"] =
            serde_json::json!([{"field": "segment", "slots": {"SMB": 0, "Mid": 1}, "overrides": {}}]);
        let svg = render_json(p);
        let point_fills: Vec<&str> = svg
            .match_indices("M3.873,0A3.873")
            .map(|(i, _)| {
                let tag = svg[i..].split("/>").next().unwrap();
                tag.split("fill=\"").nth(1).unwrap().split('"').next().unwrap()
            })
            .collect();
        assert_eq!(point_fills, ["#3164a3", "#779bc9", "#3164a3"], "{svg}");
        let fill_before_label = |label: &str| -> String {
            let marker = format!(">{label}</text>");
            let end = svg.find(&marker).unwrap_or_else(|| panic!("label {label} present: {svg}"));
            let text_start = svg[..end].rfind("<text").expect("owning <text> tag present");
            let head = &svg[..text_start];
            let fill_idx = head.rfind("fill=\"").expect("swatch fill present");
            head[fill_idx + 6..].split('"').next().unwrap().to_string()
        };
        assert_eq!(fill_before_label("Mid"), "#779bc9", "{svg}");
        assert_eq!(fill_before_label("SMB"), "#3164a3", "{svg}");
    }

    /// x is a linear scale with zero + d3 nice (default count 10): data
    /// max 39 nices the domain to [0, 40], so a "40" tick exists and the
    /// edge labels flush (first start-anchored, last end-anchored) while
    /// interior labels center.
    #[test]
    fn x_domain_nices_with_zero_and_flushes_edge_labels() {
        let svg = render_json(make_scatter_payload(false));
        assert!(svg.contains(">40</text>"), "niced domain end tick: {svg}");
        let first = svg.split("text-anchor=\"start\"").count() - 1;
        let last = svg.split("text-anchor=\"end\"").count() - 1;
        assert!(first >= 1, "flush-left first x label: {svg}");
        // "end" anchors: the last x label + the three right-axis y labels.
        assert!(last >= 4, "flush-right last x label + y labels: {svg}");
        assert!(svg.matches("text-anchor=\"middle\"").count() >= 5, "interior x labels center: {svg}");
    }

    /// Gridline positions round to whole pixels then take the +0.5 axis
    /// crispness shift (Vega tickRound); the baked y ladder is drawn as
    /// given, filtered to the baked domain.
    #[test]
    fn gridlines_round_to_half_pixel_and_y_ladder_is_baked() {
        let svg = render_json(make_scatter_payload(false));
        let grid_halves = svg.matches(".500\" y1=\"0.5\"").count();
        assert!(grid_halves >= 8, "vertical gridlines at rounded+0.5 x: {svg}");
        for lbl in ["150", "200", "250"] {
            assert!(svg.contains(&format!(">{lbl}</text>")), "baked y tick {lbl}: {svg}");
        }
    }

    /// Right legend: the vega symbol-legend geometry — circle symbols
    /// (size 100 → r 5, strokeWidth 1.5) whose rows advance by 13px, with
    /// labels 20px into the entry column.
    #[test]
    fn right_legend_uses_vega_geometry() {
        let svg = render_json(make_scatter_payload(true));
        assert_eq!(svg.matches("M5,0A5,5").count(), 2, "one circle per series: {svg}");
        let ys: Vec<f64> = svg
            .match_indices("M5,0A5,5")
            .map(|(i, _)| {
                let pre = &svg[..i];
                let t = &pre[pre.rfind("translate(").unwrap() + 10..];
                t.split(',').nth(1).unwrap().trim_end_matches(")\\\" d=\\\"").split(')').next().unwrap().parse().unwrap()
            })
            .collect();
        assert_eq!(ys[1] - ys[0], 13.0, "rows advance by 13: {ys:?}");
    }

    /// x of a `<text>` tag whose content is exactly `label`.
    fn text_x(svg: &str, label: &str) -> f64 {
        let marker = format!(">{label}</text>");
        let end = svg.find(&marker).unwrap_or_else(|| panic!("label {label} present: {svg}"));
        let tag_start = svg[..end].rfind("<text").expect("owning <text> tag present");
        attr(&svg[tag_start..end], "x")
    }

    /// The right legend's group x is `ceil(x2) + 18`, where `x2` is the y
    /// axis chrome's right edge — the plot rect's own width, the label
    /// gutter, and (when authored) the rotated title's own band. `x2` does
    /// NOT carry the axis group's `+0.5` crisp translate: `layoutAxis`
    /// translates an axis's bounds by its POSITION only (AGENTS.md's legend
    /// paragraph, "without the 0.5 crisp delta"), a different quantity from
    /// the label's own drawn x, which DOES carry it (AX1's `y_label_x =
    /// plot_w + 0.5 + y_label_edge`). `plot_size` reads `plot_w` straight off
    /// the gridline span, never through `y_label_x`, so this pins the LAW,
    /// not the implementation's own (previously wrong) choice.
    #[test]
    fn right_legend_x_is_ceil_of_the_y_axis_chrome_right_edge_plus_18() {
        let mut p = make_scatter_payload(true);
        p["resolved"]["style"]["axis_y"]["title"]["visible"] = serde_json::json!(true);
        p["resolved"]["y_label"] = serde_json::json!("Pipeline");
        let svg = render_json(p);
        let (plot_w, _) = plot_size(&svg);
        // y_label_edge, the label gutter's own width: AX1's `y_label_x =
        // plot_w + 0.5 + y_label_edge` isolates it without re-measuring font
        // metrics — a legitimate intermediate, not a re-derivation of AX2's
        // own (0.5-free) law below.
        let y_label_edge = text_x(&svg, "150") - plot_w - 0.5;
        // title.padding=10, title.font.size=11 (axis_json's title style):
        // round(0.21*11) + round(0.8*11) = 2 + 9 = 11, so y_title_extra =
        // 10 + 11 = 21 — the same padding+titleFontSize term the AGENTS.md
        // legend law documents.
        let x2 = plot_w + y_label_edge + 21.0;
        let expected = x2.ceil() + 18.0;
        let legend_x = text_x(&svg, "Segment"); // the legend title sits at the group's own x
        assert_eq!(legend_x, expected, "ceil(x2) + 18, x2 with no +0.5: {svg}");
        assert_eq!(legend_x.fract(), 0.0, "legend x is a whole pixel: {svg}");
    }

    /// Visible y tick marks reserve their length in the right-side ink and
    /// draw as 6px lines between the plot edge and the label gutter
    /// (production's label x = tickSize + labelPadding).
    #[test]
    fn visible_y_ticks_reserve_and_draw() {
        let base = render_json(make_scatter_payload(false));
        let mut json = make_scatter_payload(false);
        json["resolved"]["style"]["axis_y"]["ticks"]["visible"] = serde_json::json!(true);
        json["resolved"]["style"]["axis_y"]["ticks"]["color"] = serde_json::json!("#123123");
        let svg = render_json(json);
        assert_eq!(
            svg.matches("stroke=\"#123123\"").count(),
            3,
            "one tick line per baked y tick: {svg}"
        );
        fn label_x(svg: &str) -> f64 {
            let i = svg.find(">150</text>").unwrap();
            let tag = &svg[..i];
            let tag = &tag[tag.rfind("<text").unwrap()..];
            tag.split("x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        }
        let shift = label_x(&svg) - label_x(&base);
        // The plot also narrows by the reserved 6px, so the label anchor
        // (plot_w + tick + gutter) stays put or moves by rounding only —
        // what must hold is the tick lines spanning exactly 6px from the
        // plot edge to the gutter.
        // Strict: the un-reserved failure mode shifts the anchor by exactly
        // +6 (the plot doesn't narrow), which this rejects.
        assert!(shift.abs() < 6.0, "reservation absorbs the tick length: {shift}");
        let tick = svg.split("stroke=\"#123123\"").next().unwrap();
        let x1: f64 = tick[tick.rfind("x1=\"").unwrap() + 4..]
            .split('"').next().unwrap().parse().unwrap();
        let x2: f64 = tick[tick.rfind("x2=\"").unwrap() + 4..]
            .split('"').next().unwrap().parse().unwrap();
        assert!((x2 - x1 - 6.0).abs() < 1e-9, "tick spans its 6px length: {x1} {x2}");
    }
    /// Production's dot-plot shape: the measure on x with its authored
    /// label and format, a categorical y on the right axis (a `point`
    /// scale — no baked ladder), and the chart `sort` that orders its
    /// domain.
    fn make_dot_plot_payload(rows: serde_json::Value) -> serde_json::Value {
        let mut json = make_scatter_payload(false);
        let res = &mut json["resolved"];
        res["x"] = serde_json::json!("median_response_hours");
        res["y"] = serde_json::json!("team");
        res["x_label"] = serde_json::json!("Hours");
        res["y_label"] = serde_json::json!("Team");
        res["sort"] = serde_json::json!({"by": "median_response_hours", "order": "desc"});
        res["style"]["axis_x"]["labels"]["format"] = serde_json::json!(".1f");
        res["style"]["axis_x"]["title"]["visible"] = serde_json::json!(true);
        res["style"]["axis_y"]["tick_values"] = serde_json::json!([]);
        res["style"]["axis_y"]["domain_min"] = serde_json::Value::Null;
        res["style"]["axis_y"]["domain_max"] = serde_json::Value::Null;
        res["style"]["axis_y"]["title"]["visible"] = serde_json::json!(true);
        res["style"]["axis_y"]["is_quantitative"] = serde_json::json!(false);
        json["rows"] = rows;
        json
    }

    /// A categorical y gives the text layer's quantitative `y` no position, so
    /// Vega draws no value label (and the fit takes no label ink).
    #[test]
    fn point_value_labels_on_a_categorical_y_draw_nothing() {
        let mut json = make_dot_plot_payload(dot_rows("Charlie"));
        json["resolved"]["style"]["point_mark"]["labels"] =
            serde_json::json!({"visible": true, "font": {"family": TEST_FONT}});
        let svg = render_json(json);
        assert!(!svg.contains("data-dbt-value-label"), "{svg}");
    }

    /// Three categories whose row order (Bravo, Alpha, third), alphabetical
    /// order (Alpha, Bravo, third) and sorted-by-measure order (Bravo,
    /// third, Alpha) all differ — a band-order assertion can only pass for
    /// the chart's own `sort`.
    fn dot_rows(third: &str) -> serde_json::Value {
        serde_json::json!([
            {"median_response_hours": 3.0, "team": "Bravo"},
            {"median_response_hours": 1.0, "team": "Alpha"},
            {"median_response_hours": 2.0, "team": third},
        ])
    }

    fn render_err(json: serde_json::Value) -> String {
        let payload: Payload = serde_json::from_value(json).unwrap();
        render(&payload, &test_fonts()).expect_err("scatter refuses").to_string()
    }

    fn attr(tag: &str, key: &str) -> f64 {
        tag.split(&format!("{key}=\""))
            .nth(1)
            .unwrap_or_else(|| panic!("attribute {key} in {tag}"))
            .split('"')
            .next()
            .unwrap()
            .parse()
            .unwrap()
    }

    /// The negotiated plot box, read off the gridlines that span it.
    fn plot_size(svg: &str) -> (f64, f64) {
        let mut lines = svg.split("<line ").skip(1);
        let vline = lines.clone().find(|l| l.contains("y1=\"0.5\"")).expect("x gridline");
        let hline = lines.find(|l| l.contains("x1=\"0.5\"")).expect("y gridline");
        (attr(hline, "x2") - 0.5, attr(vline, "y2") - 0.5)
    }

    /// Every symbol's (cx, cy) in emission order.
    fn symbol_centers(svg: &str) -> Vec<(f64, f64)> {
        svg.match_indices(" d=\"M")
            .map(|(i, _)| {
                let pre = &svg[..i];
                let t = &pre[pre.rfind("translate(").unwrap() + 10..];
                let (x, rest) = t.split_once(',').unwrap();
                (x.parse().unwrap(), rest.split(')').next().unwrap().parse().unwrap())
            })
            .collect()
    }

    /// Every `<text>` as (x, y, text-anchor, content); x/y are NaN on the
    /// rotated title, which carries its position in `transform`.
    fn texts(svg: &str) -> Vec<(f64, f64, String, String)> {
        svg.split("<text ")
            .skip(1)
            .map(|chunk| {
                let (tag, rest) = chunk.split_once('>').unwrap();
                let content = rest.split_once("</text>").unwrap().0.to_string();
                let anchor = tag
                    .split("text-anchor=\"")
                    .nth(1)
                    .map(|s| s.split('"').next().unwrap().to_string())
                    .unwrap_or_default();
                (attr(tag, "x"), attr(tag, "y"), anchor, content)
            })
            .collect()
    }

    /// Every size-legend entry LABEL, in row order — everything emitted
    /// after the legend's own title text (the size legend paints last, so
    /// nothing follows it but its own entries), which keeps this from
    /// colliding with numerically-identical x/y axis tick text elsewhere
    /// in the SVG (`"20"`/`"40"` are plausible x-axis ticks too).
    fn size_legend_labels(svg: &str, title: &str) -> Vec<String> {
        let all = texts(svg);
        let i = all.iter().position(|t| t.3 == title).expect("legend title text present");
        all[i + 1..].iter().map(|t| t.3.clone()).collect()
    }

    /// A5/A3: the point scale is `padding 0.5` — step = plot_h / n, band
    /// center i = step·(0.5 + i) — and the marks stay in row order, so each
    /// row lands on its own category's band (measured: the reference's five
    /// bands at 65.4·(0.5+i) for plot_h 327).
    #[test]
    fn nominal_y_places_marks_on_the_point_scale_bands() {
        let svg = render_json(make_dot_plot_payload(dot_rows("Charlie")));
        let (_, plot_h) = plot_size(&svg);
        let step = plot_h / 3.0;
        let centers = symbol_centers(&svg);
        assert_eq!(centers.len(), 3, "one symbol per row: {svg}");
        // desc by measure: Bravo band 0, Charlie band 1, Alpha band 2.
        for (got, band) in centers.iter().zip([0.0, 2.0, 1.0]) {
            let want = step * (0.5 + band);
            assert!((got.1 - want).abs() < 1e-3, "band {band} at {want}, got {got:?}");
        }
    }

    /// The domain is the chart's `sort` applied to the distinct values, and
    /// the labels sit on the axis's own orient at `plot_edge +
    /// labelPadding`, start-anchored (measured: reference labels at x
    /// `plot_right + 6`, baseline `center + 0.5 + round(0.3·fs)`).
    #[test]
    fn nominal_y_labels_follow_the_sort_at_label_padding() {
        let svg = render_json(make_dot_plot_payload(dot_rows("Charlie")));
        let (plot_w, plot_h) = plot_size(&svg);
        let cats: Vec<_> = texts(&svg)
            .into_iter()
            .filter(|t| ["Alpha", "Bravo", "Charlie"].contains(&t.3.as_str()))
            .collect();
        assert_eq!(
            cats.iter().map(|t| t.3.as_str()).collect::<Vec<_>>(),
            ["Bravo", "Charlie", "Alpha"],
            "domain follows sort desc, not row or alphabetical order: {svg}"
        );
        let step = plot_h / 3.0;
        for (i, c) in cats.iter().enumerate() {
            assert_eq!(c.2, "start", "labels start-anchored past the plot: {c:?}");
            assert!((c.0 - (plot_w + 0.5 + 6.0)).abs() < 1e-6, "label x: {c:?}");
            let want = step * (0.5 + i as f64) + 0.5 + 3.0;
            assert!((c.1 - want).abs() < 1e-3, "label baseline {want}: {c:?}");
        }
    }

    /// Vega rounds grid positions and never labels: the band gridline sits
    /// at `round(center) + 0.5` while the mark keeps the exact center.
    #[test]
    fn nominal_y_gridlines_round_to_the_half_pixel() {
        let svg = render_json(make_dot_plot_payload(dot_rows("Charlie")));
        let (_, plot_h) = plot_size(&svg);
        let step = plot_h / 3.0;
        let centers: Vec<f64> = (0..3).map(|i| step * (0.5 + i as f64)).collect();
        assert!(
            centers.iter().any(|c| (c - c.round()).abs() > 0.05),
            "fixture must discriminate rounding: {centers:?}"
        );
        for c in &centers {
            let want = c.round() + 0.5;
            assert!(
                svg.contains(&format!("<line x1=\"0.5\" y1=\"{want:.3}\"")),
                "band gridline at {want}: {svg}"
            );
        }
    }

    /// A5: the fit arm reads the authored `x_label` and the axis's own
    /// `ticks.count` — the legacy arm derived a title from the field slug
    /// and a tick count from the plot width.
    #[test]
    fn dot_plot_x_axis_uses_the_authored_label_and_tick_count() {
        let svg = render_json(make_dot_plot_payload(dot_rows("Charlie")));
        assert!(svg.contains(">Hours</text>"), "authored x title: {svg}");
        assert!(!svg.contains("Median Response Hours"), "not the field slug: {svg}");
        let ticks: Vec<String> = texts(&svg)
            .into_iter()
            .filter(|t| t.3.starts_with(['0', '1', '2', '3']) && t.3.contains('.'))
            .map(|t| t.3)
            .collect();
        // d3 nice([0, 3], 10) = [0, 3]; ticks.count 6 -> step 0.5.
        assert_eq!(ticks, ["0.0", "0.5", "1.0", "1.5", "2.0", "2.5", "3.0"], "{svg}");
    }

    /// The right-side ink is the category labels' own width: a wider label
    /// narrows the plot and pushes the rotated title further out.
    #[test]
    fn the_widest_category_label_moves_the_right_margin() {
        let narrow = render_json(make_dot_plot_payload(dot_rows("Charlie")));
        let wide =
            render_json(make_dot_plot_payload(dot_rows("Charlie Delta Echo Foxtrot")));
        let (w_narrow, _) = plot_size(&narrow);
        let (w_wide, _) = plot_size(&wide);
        assert!(w_wide < w_narrow - 20.0, "wide label eats plot: {w_narrow} {w_wide}");
        let title_gap = |svg: &str, plot_w: f64| {
            let t = texts(svg).into_iter().find(|t| t.3 == "Team").expect("y title");
            t.0 - (plot_w + 0.5 + 6.0)
        };
        assert!(
            title_gap(&wide, w_wide) > title_gap(&narrow, w_narrow) + 20.0,
            "the title clears the widest label, not the label anchor"
        );
    }

    /// A flip-aligned right value axis sizes its gutter (production's baked
    /// `labelPadding`) from the DRAWN ladder — `axis.drawn_tick_values`, the
    /// baked rungs inside the domain. A baked rung past the domain max is
    /// never drawn and never sizes the gutter.
    #[test]
    fn right_value_gutter_measures_the_drawn_ladder_not_a_rung_past_the_domain() {
        let render_with = |ladder: serde_json::Value| {
            let mut p = make_scatter_payload(false);
            p["resolved"]["style"]["axis_y"]["tick_values"] = ladder;
            render_json(p)
        };
        let label_x = |svg: &str, text: &str| -> f64 {
            let i = svg.find(&format!(">{text}</text>")).expect("label draws");
            let tag = &svg[svg[..i].rfind("<text").unwrap()..i];
            tag.split("x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        // `.3~s` compacts the far rung to "123M", wider than any drawn label.
        let drawn = render_with(serde_json::json!([150.0, 200.0, 250.0]));
        let with_far_rung = render_with(serde_json::json!([150.0, 200.0, 250.0, 123_456_789.0]));
        assert!(!with_far_rung.contains(">123M<"), "the rung past the domain is not drawn: {with_far_rung}");
        assert_eq!(
            label_x(&with_far_rung, "250"),
            label_x(&drawn, "250"),
            "the undrawn rung must not size the gutter: {with_far_rung}"
        );
    }

    /// A scatter payload with `size` bound to `deals` — reuses
    /// `make_scatter_payload`'s color/legend/style, since production's size
    /// legend rides the SAME `chart.legend` config as the color legend
    /// (`apply_color_legend(size_enc, chart.legend)`). Row values span the
    /// exact `[0, 156]` domain `legend.rs`'s `size_legend_grid_matches_vega`
    /// probe was pinned against, so legend geometry assertions here reuse
    /// those same numbers.
    fn make_scatter_payload_with_size(with_color: bool) -> serde_json::Value {
        let mut json = make_scatter_payload(with_color);
        json["resolved"]["size"] = serde_json::json!("deals");
        json["rows"] = serde_json::json!([
            {"spend": 18.0, "pipeline": 140.0, "segment": "SMB", "deals": 0.0},
            {"spend": 27.0, "pipeline": 200.0, "segment": "Mid", "deals": 78.0},
            {"spend": 39.0, "pipeline": 250.0, "segment": "SMB", "deals": 156.0},
        ]);
        json
    }

    /// Like `make_scatter_payload_with_size`, but with caller-chosen `deals`
    /// values — the size legend's tick set/format depends only on the
    /// domain `[0, max(deals)]`, so a fixture whose domain isn't `[0, 156]`
    /// can pin the count/precision/grouping laws independently.
    fn make_scatter_payload_with_size_values(deals: &[f64]) -> serde_json::Value {
        let mut json = make_scatter_payload(true);
        json["resolved"]["size"] = serde_json::json!("deals");
        let rows: Vec<serde_json::Value> = deals
            .iter()
            .enumerate()
            .map(|(i, &d)| {
                serde_json::json!({
                    "spend": 18.0 + i as f64 * 9.0,
                    "pipeline": 140.0 + i as f64 * 30.0,
                    "segment": "SMB",
                    "deals": d,
                })
            })
            .collect();
        json["rows"] = serde_json::json!(rows);
        json
    }

    /// Vega's `data_0` filter is ONE predicate over x, y AND size — a null
    /// size value drops its row from the marks and every domain, exactly
    /// as a null x or y already does — so a payload with a null-`deals`
    /// row must render IDENTICALLY to the same payload with that row
    /// physically absent (an exact oracle: Vega compiles the two to the
    /// same spec). Regression for the round-1 review's CRITICAL-1: the
    /// `kept`/`kept_y`/`kept_size` push order let a null size value push
    /// `kept_y` while skipping `kept`, desynchronizing the parallel
    /// vectors so every LATER row's x paired with the WRONG row's y.
    #[test]
    fn a_null_size_value_drops_its_row_exactly_as_if_it_were_absent() {
        let mut with_null = make_scatter_payload_with_size(true);
        with_null["rows"] = serde_json::json!([
            {"spend": 18.0, "pipeline": 140.0, "segment": "SMB", "deals": 0.0},
            {"spend": 22.0, "pipeline": 300.0, "segment": "Mid", "deals": null},
            {"spend": 27.0, "pipeline": 200.0, "segment": "Mid", "deals": 78.0},
            {"spend": 39.0, "pipeline": 250.0, "segment": "SMB", "deals": 156.0},
        ]);
        let mut absent = make_scatter_payload_with_size(true);
        absent["rows"] = serde_json::json!([
            {"spend": 18.0, "pipeline": 140.0, "segment": "SMB", "deals": 0.0},
            {"spend": 27.0, "pipeline": 200.0, "segment": "Mid", "deals": 78.0},
            {"spend": 39.0, "pipeline": 250.0, "segment": "SMB", "deals": 156.0},
        ]);
        assert_eq!(
            render_json(with_null),
            render_json(absent),
            "a null-size row must render identically to the row being absent"
        );
    }

    /// Regression for the round-1 review's HIGH-2: the size legend's tick count
    /// was invisible to the suite — no prior test asserted the size
    /// legend's emitted LABEL TEXT, only radii and positions computed FROM
    /// the (already correct) scaled sizes, so a 5→4 mutation left every
    /// test green. Count 4 on `[0, 156]` picks d3's step-50 ladder
    /// (`"0","50","100","150"`, 4 entries), not count 5's step-20 one — the
    /// two domains disagree, so this pins the count as well as the ladder.
    #[test]
    fn size_legend_labels_pin_the_five_tick_ladder() {
        let svg = render_json(make_scatter_payload_with_size(true));
        assert_eq!(
            size_legend_labels(&svg, "Deals"),
            ["0", "20", "40", "60", "80", "100", "120", "140"]
        );
    }

    /// A fractional domain's tick step (0.2) implies one decimal of precision
    /// (`precisionFixed`), so `0.0` and `0.2` stay distinct labels.
    #[test]
    fn size_legend_labels_use_the_ticks_own_decimal_precision() {
        let svg = render_json(make_scatter_payload_with_size_values(&[0.0, 0.45, 0.9]));
        assert_eq!(
            size_legend_labels(&svg, "Deals"),
            ["0.0", "0.2", "0.4", "0.6", "0.8"]
        );
    }

    /// Regression for HIGH-2: a domain reaching into the thousands draws
    /// comma-grouped labels (Vega's own default legend format is
    /// `",f"`) — a mutant dropping the `,` from the `",.{p}f"` format spec
    /// would draw `"1000"` where this expects `"1,000"`.
    #[test]
    fn size_legend_labels_group_thousands() {
        let svg = render_json(make_scatter_payload_with_size_values(&[0.0, 2500.0, 5000.0]));
        assert_eq!(
            size_legend_labels(&svg, "Deals"),
            ["0", "1,000", "2,000", "3,000", "4,000", "5,000"]
        );
    }

    /// Vega's size scale is linear and zero-anchored over `[4, 361]` — the
    /// point mark's fixed default range, probe-confirmed independent of
    /// `mark.size`/plot geometry (probe-confirmed, 2026-09-04): the
    /// domain-max row (156) draws `√361/2 = 9.5`, the zero-anchored domain
    /// floor (0, not present in any row) draws `√4/2 = 1.0`.
    #[test]
    fn size_channel_scales_point_radius_zero_anchored() {
        let svg = render_json(make_scatter_payload_with_size(true));
        // r 1.0 draws TWICE: once as the deals=0 data mark, once as the
        // size legend's own "0" tick entry (`legend.rs`'s `size_legend_grid`
        // pinned that same symbol_radius(4) = 1.0 for the domain floor).
        assert_eq!(svg.matches("d=\"M1.000,0A1.000,1.000").count(), 2, "deals=0 -> r 1.0: {svg}");
        // r 9.5 only ever draws once: the legend's own tick set stops at
        // 140 (d3's 5-tick ladder over [0,156]), short of the domain max —
        // only the deals=156 DATA mark reaches the range top.
        assert_eq!(
            svg.matches("d=\"M9.500,0A9.500,9.500").count(),
            1,
            "deals=156 (domain max) -> r 9.5: {svg}"
        );
    }

    /// The size legend stacks BELOW the color legend at the same x, offset
    /// by its own box height plus Vega's fixed 8px inter-legend margin
    /// (probe: `legend1.y - (legend0.y + legend0.height) == 8` across
    /// varying entry counts and font sizes).
    #[test]
    fn size_legend_stacks_below_the_color_legend_at_the_same_x() {
        let svg = render_json(make_scatter_payload_with_size(true));
        let color_x = text_x(&svg, "Segment");
        let size_x = text_x(&svg, "Deals");
        assert_eq!(color_x, size_x, "both legends share the same x: {svg}");
        // Color legend: title(17) + 2 entries (SMB, Mid) at 13px advance,
        // 11px row = 17 + 11 + 13 = 41 tall.
        let color_title_y = texts(&svg).into_iter().find(|t| t.3 == "Segment").unwrap().1;
        let size_title_y = texts(&svg).into_iter().find(|t| t.3 == "Deals").unwrap().1;
        // Titles baseline at their own group's y (top-anchored); the group
        // origin is `title_y - baseline_shift`, but the DELTA between the
        // two groups' baselines equals the delta between their origins
        // since both share the same title font/baseline law.
        let color_box_h = 17.0 + 11.0 + 13.0; // title band + 1 row + advance to row 1's bottom
        assert!(
            (size_title_y - color_title_y - (color_box_h + 8.0)).abs() < 1e-6,
            "size legend at color_box_h + 8: color={color_title_y} size={size_title_y} \
             expected_delta={}: {svg}",
            color_box_h + 8.0
        );
    }

    #[test]
    fn a_field_label_titles_the_color_and_size_legends() {
        let mut json = make_scatter_payload_with_size(true);
        json["resolved"]["field_labels"] = serde_json::json!({"segment": "Tier", "deals": "Won deals"});
        let svg = render_json(json);
        assert!(svg.contains(">Tier<") && svg.contains(">Won deals<"), "{svg}");
        assert!(!svg.contains(">Segment<") && !svg.contains(">Deals<"), "{svg}");
    }

    /// `(x, y)` of the last `<text>` whose body is `body`: the legend paints
    /// after the axes, whose tick labels share its numerals.
    fn text_xy(svg: &str, body: &str) -> (f64, f64) {
        let el = svg
            .split("<text ")
            .skip(1)
            .filter(|el| el.find('>').is_some_and(|at| el[at + 1..].starts_with(&format!("{body}</text>"))))
            .last()
            .unwrap_or_else(|| panic!("no <text> {body:?} in {svg}"));
        let num = |name: &str| -> f64 {
            let key = format!("{name}=\"");
            let at = el.find(&key).unwrap() + key.len();
            el[at..at + el[at..].find('"').unwrap()].parse().unwrap()
        };
        (num("x"), num("y"))
    }

    fn legend_with_size(edge: &str) -> String {
        let mut json = make_scatter_payload_with_size(true);
        json["resolved"]["legend"]["position"] = serde_json::json!({"edge": edge, "align": "start", "overlay": false});
        if edge != "right" {
            json["resolved"]["legend"]["direction"] = serde_json::json!("horizontal");
        }
        render_json(json)
    }

    /// Beside a bottom edge the legends flow along it: the size legend opens in
    /// the color legend's row, one `VEGA_LEGEND_GROUP_MARGIN` past its width,
    /// and its entries are one row of columns left to right.
    #[test]
    fn a_size_legend_follows_a_bottom_legend() {
        let svg = legend_with_size("bottom");
        let fonts = test_fonts();
        let last_label = text_xy(&svg, "SMB");
        let color_w = (last_label.0 + fonts.measure_shaped("SMB", TEST_FONT, 11.0).unwrap()).ceil();
        let color_title = text_xy(&svg, "Segment");
        let size_title = text_xy(&svg, "Deals");
        assert_eq!(size_title.1, color_title.1, "both titles open on the legend row");
        assert_eq!(size_title.0, color_w + crate::families::legend::VEGA_LEGEND_GROUP_MARGIN, "the size legend opens one margin past the color legend");
        let labels: Vec<(f64, f64)> = ["0", "20", "40", "60", "80", "100", "120", "140"].iter().map(|l| text_xy(&svg, l)).collect();
        assert!(labels.windows(2).all(|w| w[1].0 > w[0].0), "one column per entry: {labels:?}");
        assert!(labels.iter().all(|l| (l.1 - labels[0].1).abs() < 1.0), "one row: {labels:?}");
        assert!(labels[0].1 > size_title.1, "the entries sit under the title row");
    }

    /// Beside a side edge the legends flow downward from one shared origin: the
    /// size legend opens `VEGA_LEGEND_GROUP_MARGIN` under the color legend's
    /// box (a 17px title band plus two 11px entries, 2px apart).
    #[test]
    fn a_size_legend_stacks_under_a_right_legend() {
        let svg = legend_with_size("right");
        let color_title = text_xy(&svg, "Segment");
        let size_title = text_xy(&svg, "Deals");
        assert_eq!(size_title.0, color_title.0, "one shared column origin");
        assert_eq!(size_title.1 - color_title.1, 17.0 + 2.0 * 11.0 + 2.0 + crate::families::legend::VEGA_LEGEND_GROUP_MARGIN);
        let labels: Vec<f64> = ["0", "20", "40", "60", "80", "100", "120", "140"].iter().map(|l| text_xy(&svg, l).1).collect();
        assert!(labels.windows(2).all(|w| w[1] > w[0]), "one row per entry: {labels:?}");
        assert!(labels[0] > size_title.1, "the entries sit under the title");
    }

    /// A shape channel on a categorical y draws one symbol per category of its
    /// own scale, in the marks and in the shape legend.
    #[test]
    fn a_shape_channel_on_a_categorical_y_draws_its_symbols() {
        let mut json = make_dot_plot_payload(serde_json::json!([
            {"median_response_hours": 3.0, "team": "Bravo", "kind": "m"},
            {"median_response_hours": 1.0, "team": "Alpha", "kind": "n"},
            {"median_response_hours": 2.0, "team": "Charlie", "kind": "m"},
        ]));
        json["resolved"]["shape"] = serde_json::json!("kind");
        json["board_style"] = serde_json::json!({
            "font": TEST_FONT, "text_mark_font": {"family": TEST_FONT, "size": 11.0}, "secondary_color": null, "tones": null,
            "title_case": null, "continuous_height": null,
            "format_locale": crate::number_locale::en_us_wire(), "time_locale": crate::time_locale::en_us_wire()
        });
        let svg = render_json(json);
        assert!(svg.contains("h7.746v7.746"), "a square mark for the second category: {svg}");
        assert!(svg.contains("M-5,-5h10v10h-10Z"), "the legend's square glyph: {svg}");
    }

    /// A shape legend always stacks at the right, whatever edge the color
    /// legend takes: the color legend sits under the plot, the shape legend
    /// beyond the plot's right edge at the top.
    #[test]
    fn a_shape_legend_stays_at_the_right_of_a_bottom_legend() {
        let mut json = make_scatter_payload(true);
        json["resolved"]["shape"] = serde_json::json!("region");
        json["board_style"] = serde_json::json!({
            "font": TEST_FONT, "text_mark_font": {"family": TEST_FONT, "size": 11.0}, "secondary_color": null, "tones": null,
            "title_case": null, "continuous_height": null,
            "format_locale": crate::number_locale::en_us_wire(), "time_locale": crate::time_locale::en_us_wire()
        });
        for (i, row) in json["rows"].as_array_mut().unwrap().iter_mut().enumerate() {
            row["region"] = serde_json::json!(["East", "West"][i % 2]);
        }
        json["resolved"]["legend"]["position"] = serde_json::json!({"edge": "bottom", "align": "start", "overlay": false});
        let svg = render_json(json);
        let color_title = text_xy(&svg, "Segment");
        let shape_title = text_xy(&svg, "region");
        let plot_right = text_xy(&svg, "250").0;
        assert!(color_title.1 > 300.0 && color_title.0 == 0.0, "the color legend opens under the plot: {color_title:?}");
        assert!(shape_title.0 > plot_right, "the shape legend sits beyond the y axis labels: {shape_title:?}");
        assert_eq!(shape_title.1, 9.0, "and opens at the top of the legend column");
        let rows = [text_xy(&svg, "East").1, text_xy(&svg, "West").1];
        assert_eq!(rows[1] - rows[0], 16.0, "one row per entry: {rows:?}");
    }

    /// A size cell JS reads as `NaN` drops its row like a null one: the chart
    /// renders as the same payload with that row absent.
    #[test]
    fn non_numeric_size_value_drops_its_row() {
        let mut json = make_scatter_payload_with_size(true);
        let mut without = json.clone();
        without["rows"].as_array_mut().unwrap().remove(0);
        json["rows"][0]["deals"] = serde_json::json!("lots");
        assert_eq!(render_json(json), render_json(without));
    }

    /// An all-zero size column is a zero-width domain `[0, 0]`: d3 maps every
    /// value to the middle of the range, so each mark takes the middle area.
    #[test]
    fn degenerate_size_domain_draws_the_middle_of_the_range() {
        let svg = render_json(make_scatter_payload_with_size_values(&[0.0, 0.0, 0.0]));
        let (r_lo, r_hi) = super::SIZE_SCALE_RANGE;
        let radius = super::symbol_radius((r_lo + r_hi) / 2.0);
        let expected = format!("d=\"M{radius:.3},0A");
        // Three marks and the legend's one entry.
        assert_eq!(svg.matches(&expected).count(), 4, "{svg}");
    }

    /// A nominal x over a measure with no baked ladder (the dot plot
    /// fixture's own empty one) draws Vega's own ticks, `ceil(height / 40)` of
    /// them over the plot the labels settle.
    #[test]
    fn nominal_x_over_an_unbaked_ladder_draws_vegas_own_ticks() {
        let mut json = make_dot_plot_payload(dot_rows("Charlie"));
        json["resolved"]["x"] = serde_json::json!("team");
        json["resolved"]["y"] = serde_json::json!("median_response_hours");
        let svg = render_json(json);
        let (_, plot_h) = plot_size(&svg);
        let rungs = svg.matches("text-anchor=\"end\"").count();
        assert_eq!(rungs as f64, d3rs::ticks::d3_ticks(0.0, 3.0, (plot_h / 40.0).ceil() as usize).len() as f64, "{svg}");
    }

    /// A nominal x together with a nominal y draws on two point scales
    /// (`axis-scale/41`).
    #[test]
    fn nominal_x_and_nominal_y_renders() {
        let mut json = make_dot_plot_payload(dot_rows("Charlie"));
        json["resolved"]["x"] = serde_json::json!("team");
        let payload: Payload = serde_json::from_value(json).unwrap();
        render(&payload, &test_fonts()).expect("renders");
    }

    /// A dot plot on a left y axis end-anchors its category labels at the
    /// plot's left edge, and the plot starts past their widest.
    #[test]
    fn a_dot_plot_on_a_left_axis_end_anchors_its_labels_left_of_the_plot() {
        let mut json = make_dot_plot_payload(dot_rows("Charlie"));
        json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
        let svg = render_json(json);
        let label = svg.split("<text ").find(|t| t.contains(">Charlie</text>")).expect("a category label");
        assert!(label.contains("text-anchor=\"end\""), "{label}");
        assert!(attr(label, "x") <= 0.5, "left of the axis group's origin: {label}");
    }

    /// A value axis on the left puts the fit arm's tick labels end-anchored
    /// `labelPadding` left of the axis, and the plot clears the widest.
    #[test]
    fn a_left_value_axis_end_anchors_its_labels_left_of_the_plot() {
        let mut json = make_scatter_payload(false);
        json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
        json["resolved"]["style"]["point_mark"]["shape"] = serde_json::json!("square");
        let svg = render_json(json);
        let label = svg.split("<text ").find(|t| t.contains(">200</text>")).expect("a value label");
        assert!(label.contains("text-anchor=\"end\""), "{label}");
        assert!(attr(label, "x") < 0.0, "left of the axis group's origin: {label}");
        let plot = svg.split("<g transform=\"translate(").nth(1).expect("the plot group");
        let origin: f64 = plot.split(',').next().unwrap().parse().unwrap();
        assert!(origin > 16.0 + 20.0, "the plot starts past the label gutter: {origin}");
    }

    /// Authored `scale.values` keep every label (`noSkip`), where the same ladder blanks its minors.
    #[test]
    fn authored_log_values_keep_every_mantissa_labeled() {
        let ladder = |values: Option<serde_json::Value>| {
            let mut json = make_scatter_payload(false);
            let mut scale = serde_json::json!({"continuous": {"type": "log", "domain": [1e8, 1e10]}});
            if let Some(v) = values {
                scale["values"] = v;
            }
            json["resolved"]["style"]["axis_y"]["scale"] = scale;
            let payload: Payload = serde_json::from_value(json).unwrap();
            let r: ScatterResolved = serde::Deserialize::deserialize(&payload.resolved).unwrap();
            LogAxis::new(&r.style.axis_y, 1e8, 1e10, 10, None, 300.0).unwrap().mantissa_visible
        };
        assert!(ladder(None).contains(&false), "the default ladder blanks its minors");
        assert!(ladder(Some(serde_json::json!([2e8, 6e8, 1e9]))).iter().all(|v| *v));
    }

    /// An authored log domain keeps its order: a descending pair inverts the
    /// axis, and d3 hands the ticks back reversed.
    #[test]
    fn a_descending_log_domain_inverts_the_axis_and_reverses_its_ticks() {
        let mut json = make_scatter_payload(false);
        json["resolved"]["style"]["axis_y"]["scale"] = serde_json::json!({"continuous": {"type": "log", "domain": [1000.0, 10.0]}});
        let payload: Payload = serde_json::from_value(json).unwrap();
        let r = ScatterResolved::from_payload(&payload).unwrap();
        let ladder = LogAxis::new(&r.style.axis_y, 140.0, 250.0, 10, None, 100.0).unwrap();
        assert_eq!(ladder.ticks.first(), Some(&1000.0));
        assert_eq!(ladder.ticks.last(), Some(&10.0));
        assert_eq!(ladder.frac_from_top(1000.0), 1.0, "the large end sits at the bottom of an inverted axis");
        assert_eq!(ladder.frac_from_top(10.0), 0.0);
    }

    /// Vega pads a continuous scale's domain about its center so the data ends
    /// sit `padding` pixels inside the range; a quantitative x then rounds the
    /// padded domain out.
    #[test]
    fn a_padded_linear_x_domain_widens_with_the_plot_width() {
        let lin = LinearX {
            raw: (0.0, 10.0),
            zero: false,
            nice: false,
            fit: false,
            pad: 10.0,
            bend: crate::vega_fit::Bend::Linear,
            tick_count: 5,
            min_step: None,
            format: None,
            ruler: None,
            tick_label: None,
            expr: None,
            case: None,
            rule_datums: [None, None],
        };
        let (ticks, labels, _, domain) = lin.at(120.0).unwrap();
        assert_eq!(domain, (-1.0, 11.0), "10 of 120 px each side widens by 120 / 100");
        assert_eq!(lin.frac(domain, 0.0), 1.0 / 12.0);
        assert_eq!(labels.first().map(String::as_str), Some("0"), "d3's default tick format: {labels:?} {ticks:?}");
        let (_, _, _, wider) = lin.at(220.0).unwrap();
        assert!(wider.0 > domain.0, "a wider plot needs less padding: {wider:?}");
    }

    /// With no authored label format an x tick reads d3's default: grouped
    /// fixed point at the precision the tick step implies.
    #[test]
    fn an_x_without_a_label_format_reads_d3s_default_tick_format() {
        let lin = LinearX { raw: (0.0, 3000.0), zero: false, nice: true, fit: false, pad: 0.0, bend: crate::vega_fit::Bend::Linear, tick_count: 3, min_step: None, format: None, ruler: None, tick_label: None, expr: None, case: None, rule_datums: [None, None] };
        let (_, labels, _, _) = lin.at(400.0).unwrap();
        assert_eq!(labels, ["0", "1,000", "2,000", "3,000"]);
        let lin = LinearX { raw: (0.0, 1.0), zero: false, nice: true, fit: false, pad: 0.0, bend: crate::vega_fit::Bend::Linear, tick_count: 2, min_step: None, format: None, ruler: None, tick_label: None, expr: None, case: None, rule_datums: [None, None] };
        let (_, labels, _, _) = lin.at(400.0).unwrap();
        assert_eq!(labels, ["0.0", "0.5", "1.0"]);
    }

    /// A nominal x IN the fit shape (right y axis, baked measure ladder —
    /// `make_scatter_payload`'s own default `pipeline` axis_y) places marks
    /// on Vega's point scale, same law as the nominal-y dot plot but on x:
    /// step = plot_w / (n - 1) at the theme's `axis_x.scale.padding: 0`
    /// (edge-to-edge bands, not the y dot plot's own hardcoded 0.5).
    #[test]
    fn nominal_x_places_marks_on_the_point_scale_bands() {
        let mut json = make_scatter_payload(false);
        json["resolved"]["x"] = serde_json::json!("team");
        json["resolved"]["style"]["axis_x"]["scale"] = serde_json::json!({"padding": 0.0});
        json["rows"] = serde_json::json!([
            {"team": "Bravo", "pipeline": 140.0},
            {"team": "Alpha", "pipeline": 200.0},
            {"team": "Charlie", "pipeline": 250.0},
        ]);
        let svg = render_json(json);
        let (plot_w, _) = plot_size(&svg);
        let centers = symbol_centers(&svg);
        assert_eq!(centers.len(), 3, "one symbol per row: {svg}");
        // padding 0, n=3: band(i) = i / (n - 1) -- edge-to-edge. No chart
        // `sort` authored, so the domain is alphabetical (Alpha, Bravo,
        // Charlie), not row order (Bravo, Alpha, Charlie).
        for (got, band) in centers.iter().zip([1.0, 0.0, 2.0]) {
            let want = plot_w * (band / 2.0);
            assert!((got.0 - want).abs() < 1e-3, "band {band} at {want}, got {got:?}");
        }
    }

    /// `infer_vega_type_from_data` samples a column's first ten cells: a numeric
    /// column holding a later string types quantitative, and Vega-Lite's
    /// invalid filter drops the string row rather than turning x into a band.
    #[test]
    fn a_later_string_in_a_numeric_x_drops_its_row() {
        let mut json = make_scatter_payload(false);
        let rows = json["rows"].as_array_mut().unwrap();
        let numeric: Vec<_> = (0..10).map(|i| serde_json::json!({"spend": 18.0 + i as f64, "pipeline": 140.0 + i as f64})).collect();
        *rows = numeric;
        rows.push(serde_json::json!({"spend": "n/a", "pipeline": 220.0}));
        let svg = render_json(json);
        assert_eq!(symbol_centers(&svg).len(), 10, "the eleventh row is dropped: {svg}");
        assert!(!svg.contains(">n/a<"), "x stays a linear scale: {svg}");
    }

    /// The converse: a first-ten sample holding a string types the column
    /// nominal, so a numeric cell is one more category on the band.
    #[test]
    fn a_leading_string_types_x_nominal() {
        let mut json = make_scatter_payload(false);
        json["resolved"]["x"] = serde_json::json!("team");
        json["resolved"]["style"]["axis_x"]["scale"] = serde_json::json!({"padding": 0.0});
        json["rows"] = serde_json::json!([
            {"team": "Bravo", "pipeline": 140.0},
            {"team": 7.0, "pipeline": 200.0},
        ]);
        let svg = render_json(json);
        assert_eq!(symbol_centers(&svg).len(), 2, "{svg}");
        assert!(svg.contains(">Bravo<"), "x is a band: {svg}");
    }

    /// The `!x_nominal` half of the widened title-overflow gate: a nominal-x
    /// scatter with a title/subtitle production wraps to two lines takes the
    /// SAME fit-arm wrap-two law the nominal-y dot plot does, never the
    /// legacy fallback — whose point loop reads a numeric x and would draw
    /// no marks and no category labels at all (this file's own dispatch
    /// comment). Same fixture as `nominal_x_places_marks_on_the_point_scale_
    /// bands`, same oversized title as `a_dot_plot_with_a_wrapping_title_
    /// wraps_instead_of_refusing`.
    #[test]
    fn a_nominal_x_scatter_with_a_wrapping_title_wraps_instead_of_refusing() {
        let mut json = make_scatter_payload(false);
        json["resolved"]["x"] = serde_json::json!("team");
        json["resolved"]["style"]["axis_x"]["scale"] = serde_json::json!({"padding": 0.0});
        json["rows"] = serde_json::json!([
            {"team": "Bravo", "pipeline": 140.0},
            {"team": "Alpha", "pipeline": 200.0},
            {"team": "Charlie", "pipeline": 250.0},
        ]);
        json["resolved"]["title"] = serde_json::json!(
            "Median response time by team, by quarter, across every support region \
             and escalation tier we track"
        );
        json["resolved"]["title_style"] = serde_json::json!({
            "font": {"family": TEST_FONT, "size": 18.0, "weight": "500", "color": "#494A4C"},
            "position": {"offset": 10.0}
        });
        let svg = render_json(json);
        // Marks and category labels still paint — proof this took the fit
        // arm, not `render_legacy` (which reads a numeric x and draws
        // neither).
        assert_eq!(symbol_centers(&svg).len(), 3, "{svg}");
        assert!(
            svg.contains(">Alpha<") && svg.contains(">Bravo<") && svg.contains(">Charlie<"),
            "{svg}"
        );
        // Two wrapped title lines, same advance the dot-plot twin pins.
        assert!(
            svg.contains(
                "data-authored-kind=\"title\">Median response time by team, \
                 by quarter, across every support</text>"
            ),
            "{svg}"
        );
        assert!(svg.contains(">region and escalation tier we track</text>"), "{svg}");
        assert_eq!(svg.matches("data-authored-kind=\"title\"").count(), 1, "{svg}");
        let tag_ending_in = |needle: &str| -> &str {
            let end = svg.find(needle).unwrap() + needle.len();
            let start = svg[..end].rfind("<text").unwrap();
            &svg[start..end]
        };
        let first_line_y = attr(tag_ending_in("across every support</text>"), "y");
        let second_line_y = attr(tag_ending_in("region and escalation tier we track</text>"), "y");
        assert_eq!(
            second_line_y - first_line_y,
            18.0 + crate::vega_fit::TitleInk::LINE_ADVANCE_EXTRA,
            "{svg}"
        );
    }

    /// The wide-measure "dot/band plot" — `y: [a, b]` folded onto a nominal
    /// x, the shape `charts/point-charts_5::actual_vs_target`'s corpus row
    /// exercises: one fold, `common::unfold_wide_rows` + `wide_measure_
    /// labels_for`, both a bar/line-shared function, applied once at the
    /// top of `render` before the fit-arm gate ever runs. Two points per
    /// category, colored by the RAW-sorted (`total_cost` < `unit_price`)
    /// measure identity, painted at palette slots 0/1 in that order.
    fn make_wide_dot_band_payload() -> serde_json::Value {
        let mut json = make_scatter_payload(false);
        let res = &mut json["resolved"];
        res["x"] = serde_json::json!("team");
        res["y"] = serde_json::json!("__dbt_charts_wide_value__");
        res["wide_measures"] = serde_json::json!(["unit_price", "total_cost"]);
        res["style"]["axis_x"]["scale"] = serde_json::json!({"padding": 0.0});
        res["style"]["axis_y"]["tick_values"] = serde_json::json!([0.0, 20.0, 40.0, 60.0, 80.0]);
        res["style"]["axis_y"]["domain_min"] = serde_json::json!(5.0);
        res["style"]["axis_y"]["domain_max"] = serde_json::json!(75.0);
        res["legend"] = serde_json::json!({
            "visible": true, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
            "title": {"font": {"case": "title", "family": TEST_FONT, "size": 12.0, "weight": "600", "color": "#313233"}},
            "label": {"font": {"family": TEST_FONT, "size": 11.0, "weight": "400", "color": "#626366"}, "padding": 8.0}
        });
        json["rows"] = serde_json::json!([
            {"team": "Alpha", "unit_price": 10.0, "total_cost": 50.0},
            {"team": "Bravo", "unit_price": 20.0, "total_cost": 60.0},
            {"team": "Charlie", "unit_price": 30.0, "total_cost": 70.0},
        ]);
        json
    }

    #[test]
    fn wide_measures_fold_places_two_colored_points_per_nominal_x_band() {
        let svg = render_json(make_wide_dot_band_payload());
        // `symbol_centers` also picks up the legend's own 2 swatch circles
        // (same `d="M...A..."` shape) -- 6 data points + 2 swatches.
        assert_eq!(symbol_centers(&svg).len(), 8, "3 categories x 2 measures + legend: {svg}");
        // total_cost < unit_price raw-sorted -- palette[0]/palette[1] in
        // that order, one fill per measure regardless of category. Matched
        // on the point's own radius (3.873, from `symbol_radius(60.0)`) so
        // the legend's own same-colored swatch (radius 5) isn't counted.
        assert_eq!(
            svg.matches("3.873,0\" fill=\"#3164a3\"").count(),
            3,
            "total_cost points: {svg}"
        );
        assert_eq!(
            svg.matches("3.873,0\" fill=\"#779bc9\"").count(),
            3,
            "unit_price points: {svg}"
        );
    }

    /// Legend entries read the HUMANIZED measure name (`wide_measure_
    /// labels_for`, mirrors production's `_label_expression`), never the
    /// raw column identity or the synthetic fold field name — and the
    /// fold's own legend title is always empty (`_wide.py::fold_wide_
    /// measures` bakes `title: None` unconditionally), never a
    /// `slug_to_display` guess off `WIDE_LABEL_FIELD`.
    #[test]
    fn wide_measures_legend_shows_humanized_labels_and_no_title() {
        let svg = render_json(make_wide_dot_band_payload());
        assert!(svg.contains(">Total cost<"), "{svg}");
        assert!(svg.contains(">Unit price<"), "{svg}");
        assert!(!svg.contains("total_cost") && !svg.contains("unit_price"), "{svg}");
        assert!(!svg.contains(crate::common::WIDE_LABEL_FIELD), "{svg}");
        // The legend title's own font (size 12, weight 600) paints nowhere
        // in this chart when there's no title to paint — axis titles are
        // hidden (`axis_json`'s `title.visible: false`) and every axis
        // label/legend label runs at a different size/weight, so this
        // combination is unique to a drawn legend title.
        assert!(!svg.contains("font-size=\"12\" font-weight=\"600\""), "{svg}");
        // Exactly two legend swatches (the shared legend's 5px symbol radius,
        // distinct from the data points' own √60/2 ≈ 3.873 radius) confirm the legend
        // starts right at the entries, no title row consuming a slot above.
        assert_eq!(svg.matches("d=\"M5,0A5,5").count(), 2, "{svg}");
    }

    /// A wide scatter's `StructuredTooltipFeature` refuses outright in
    /// production (`features/structured_tooltip.py`), so its per-mark aria
    /// falls back to Vega's own default `encoding.tooltip` join: plain
    /// "title: value" rows, x then the folded value then `Series: <label>`,
    /// joined "; " — never the swatched `ROLE_HEADER_SWATCHED` marker the
    /// structured branch stamps on an authored `color:` field.
    #[test]
    fn wide_measures_point_aria_label_is_vegas_plain_tooltip_join() {
        let mut json = make_wide_dot_band_payload();
        json["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.2~f");
        let svg = render_json(json);
        assert!(
            svg.contains(
                "aria-label=\"Team: Alpha; Unit price, Total cost: 10; Series: Unit price\""
            ),
            "{svg}"
        );
        assert!(!svg.contains('\u{2064}'), "no ROLE_HEADER_SWATCHED marker: {svg}");
    }

    #[test]
    fn wide_measures_point_aria_label_names_the_series_in_the_board_language() {
        let mut json = make_wide_dot_band_payload();
        json["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.2~f");
        json["board_style"] = serde_json::json!({
            "font": TEST_FONT, "text_mark_font": {"family": TEST_FONT, "size": 11.0}, "secondary_color": null, "tones": null,
            "title_case": null, "continuous_height": null,
            "format_locale": crate::number_locale::en_us_wire(), "language": "de", "time_locale": crate::time_locale::en_us_wire()
        });
        let svg = display_text::language::with_language(display_text::language::Language::new("de").unwrap(), || {
            render_json(json)
        });
        assert!(svg.contains("; Serie: Unit price\""), "{svg}");
        assert!(!svg.contains("Series:"), "{svg}");
    }

    /// `wide_measures` beside an authored `color:` dimension composes the
    /// fold's `<value> - <measure>` series, humanized on the measure part.
    #[test]
    fn wide_measures_with_an_authored_color_dimension_compose_series_labels() {
        let mut json = make_wide_dot_band_payload();
        json["resolved"]["color"] = serde_json::json!("team");
        let svg = render_json(json);
        assert!(svg.contains(">Alpha - Unit price</text>"), "{svg}");
    }

    /// A `size` channel on a wide scatter sizes the folded points.
    #[test]
    fn wide_measures_with_a_size_channel_render() {
        let mut json = make_wide_dot_band_payload();
        json["resolved"]["size"] = serde_json::json!("unit_price");
        let svg = render_json(json);
        assert!(symbol_centers(&svg).len() >= 6, "{svg}");
    }

    /// Review round 1 HIGH 1: scatter's fold order is always RAW-sorted
    /// (`_wide_scatter_fold`'s own doc comment; `_measure_paint_order`
    /// returns `raw_wide_series_names` = `sorted(measures)` unchanged when
    /// there is no dimension), never the AUTHORED `wide_measures` array
    /// order. `make_wide_dot_band_payload` authors `[unit_price,
    /// total_cost]` — alphabetically unsorted — so a correct fold paints
    /// `total_cost` FIRST in every category (palette slot 0, `#3164a3`),
    /// not `unit_price` (`#779bc9`).
    #[test]
    fn wide_measures_fold_paints_sorted_measure_order_not_authored_order() {
        let svg = render_json(make_wide_dot_band_payload());
        let marker = "3.873,0\" fill=\"";
        let start = svg.find(marker).expect("a data point marker") + marker.len();
        let first_fill = &svg[start..start + 7];
        assert_eq!(first_fill, "#3164a3", "total_cost paints first (sorted): {svg}");
    }

    fn make_wide_quantitative_x_payload() -> serde_json::Value {
        let mut json = make_wide_dot_band_payload();
        json["resolved"]["x"] = serde_json::json!("spend");
        json["rows"] = serde_json::json!([
            {"spend": 10.0, "unit_price": 10.0, "total_cost": 50.0},
            {"spend": 20.0, "unit_price": 20.0, "total_cost": 60.0},
            {"spend": 30.0, "unit_price": 30.0, "total_cost": 70.0},
        ]);
        json
    }

    /// A wide scatter over a quantitative x folds each measure into its own point per row.
    #[test]
    fn wide_measures_with_a_quantitative_x_render() {
        let mut json = make_wide_quantitative_x_payload();
        json["resolved"]["style"]["axis_x"]["labels"]["format"] = serde_json::json!(",.2f");
        let svg = render_json(json);
        assert_eq!(symbol_centers(&svg).len(), 8, "three rows x two measures, plus two legend keys: {svg}");
    }

    /// A wide scatter's tooltip prints a quantitative x as Vega prints the number
    /// (`10`, not `10.0`) and a date x through its unit's `timeUnit` text.
    #[test]
    fn wide_scatter_tooltips_print_the_x_as_vega_does() {
        let mut json = make_wide_quantitative_x_payload();
        json["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.0f");
        let svg = render_json(json);
        let aria = |svg: &str| svg.split("aria-label=\"").skip(1).filter_map(|a| a.split('"').next()).filter(|a| a.contains("; ")).map(str::to_string).collect::<Vec<_>>();
        let labels = aria(&svg);
        assert!(!labels.is_empty(), "{svg}");
        assert!(labels.iter().all(|a| !a.contains("10.0")), "{labels:?}");
        assert!(labels.iter().any(|a| a.contains(": 10; ")), "{labels:?}");

        let _locale = crate::time_locale::en_us_scope();
        let mut dated = make_wide_quantitative_x_payload();
        dated["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.0f");
        dated["resolved"]["style"]["label_usable_ratio"] = serde_json::json!(0.85);
        for (row, day) in dated["rows"].as_array_mut().expect("rows").iter_mut().zip(["2024-01-15", "2024-02-15", "2024-03-15"]) {
            row["spend"] = serde_json::json!(day);
        }
        let labels = aria(&render_json(dated));
        assert!(labels.iter().any(|a| a.contains(": Jan 2024; ")), "{labels:?}");
    }

    /// Month labels over daily dates name their period: centered between boundary ticks (the short name, the year on
    /// the first), not d3's full month name on the tick.
    #[test]
    fn a_time_axis_names_months_between_boundary_ticks() {
        let _locale = crate::time_locale::en_us_scope();
        let mut json = make_scatter_payload(false);
        json["resolved"]["style"]["label_usable_ratio"] = serde_json::json!(0.85);
        json["resolved"]["style"]["axis_x"]["labels"]["time_unit"] = serde_json::json!("yearmonth");
        json["resolved"]["style"]["axis_x"]["time_unit"] = serde_json::json!("yearmonthdate");
        let days = (0..60).map(|i| {
            let d = crate::families::temporal::civil_from_days(crate::families::temporal::days_from_civil(2026, 1, 1) + 3 * i);
            serde_json::json!({"spend": format!("{:04}-{:02}-{:02}", d.0, d.1, d.2), "pipeline": 100.0 + i as f64})
        });
        json["rows"] = serde_json::Value::Array(days.collect());
        let svg = render_json(json);
        for label in ["Feb", "Jun", "2026"] {
            assert!(svg.contains(&format!(">{label}</text>")), "no {label} label");
        }
        assert!(!svg.contains(">February</text>"), "d3's full month name sits on the tick");
    }

    /// An authored `labels.align` turns interval mode off: d3's month names stay on the ticks instead of interval quarters.
    #[test]
    fn an_authored_label_align_turns_interval_labels_off() {
        let _locale = crate::time_locale::en_us_scope();
        let mut json = make_scatter_payload(false);
        json["resolved"]["style"]["label_usable_ratio"] = serde_json::json!(0.85);
        json["resolved"]["style"]["axis_x"]["labels"]["time_unit"] = serde_json::json!("yearquarter");
        json["resolved"]["style"]["axis_x"]["labels"]["align"] = serde_json::json!("left");
        json["resolved"]["style"]["axis_x"]["ticks"]["count"] = serde_json::Value::Null;
        let days = (0..60).map(|i| {
            let d = crate::families::temporal::civil_from_days(crate::families::temporal::days_from_civil(2026, 1, 1) + 30 * i);
            serde_json::json!({"spend": format!("{:04}-{:02}-{:02}", d.0, d.1, d.2), "pipeline": 100.0 + i as f64})
        });
        json["rows"] = serde_json::Value::Array(days.collect());
        let svg = render_json(json);
        let grid = regex::Regex::new(r#"role-axis-grid.*?</g>"#).expect("pattern").find(&svg).expect("grid").as_str().to_string();
        let ticks: Vec<f64> = regex::Regex::new(r#"x1="([\d.]+)""#).expect("pattern").captures_iter(&grid).map(|c| c[1].parse().expect("x1")).collect();
        let labels: Vec<f64> = regex::Regex::new(r#"<text x="([\d.]+)"[^>]*>Q2</text>"#).expect("pattern").captures_iter(&svg).map(|c| c[1].parse().expect("x")).collect();
        assert!(!labels.is_empty(), "no Q2 labels: {svg}");
        for at in labels {
            assert!(ticks.iter().any(|t| (t - at).abs() < 1.0), "Q2 at {at} is off the ticks {ticks:?}");
        }
    }

    /// An authored `labels.angle` takes effect: it turns interval mode off rather than being dropped by it.
    #[test]
    fn an_authored_angle_on_a_grainless_date_axis_is_kept() {
        let _locale = crate::time_locale::en_us_scope();
        let mut json = make_scatter_payload(false);
        json["resolved"]["style"]["label_usable_ratio"] = serde_json::json!(0.85);
        json["resolved"]["style"]["axis_x"]["labels"]["time_unit"] = serde_json::json!("year");
        json["resolved"]["style"]["axis_x"]["labels"]["angle"] = serde_json::json!(-45);
        json["resolved"]["style"]["axis_x"]["ticks"]["count"] = serde_json::Value::Null;
        let days = (0..60).map(|i| {
            let d = crate::families::temporal::civil_from_days(crate::families::temporal::days_from_civil(2026, 1, 1) + 90 * i);
            serde_json::json!({"spend": format!("{:04}-{:02}-{:02}", d.0, d.1, d.2), "pipeline": 100.0 + i as f64})
        });
        json["rows"] = serde_json::Value::Array(days.collect());
        let svg = render_json(json);
        assert!(svg.contains("rotate(-45"), "authored angle dropped");
    }

    /// Daily scatter rows from 2026-03-03 over `days` days, 600px wide, on a daily-grain time axis.
    fn daily_scatter_labels(days: i64, label_unit: Option<&str>) -> Vec<String> {
        let _locale = crate::time_locale::en_us_scope();
        let mut json = make_scatter_payload(false);
        json["resolved"]["style"]["label_usable_ratio"] = serde_json::json!(0.85);
        json["resolved"]["style"]["axis_x"]["time_unit"] = serde_json::json!("yearmonthdate");
        json["resolved"]["style"]["axis_x"]["ticks"]["count"] = serde_json::Value::Null;
        if let Some(unit) = label_unit {
            json["resolved"]["style"]["axis_x"]["labels"]["time_unit"] = serde_json::json!(unit);
        }
        let first = crate::families::temporal::days_from_civil(2026, 3, 3);
        let rows = (0..days).map(|i| {
            let d = crate::families::temporal::civil_from_days(first + i);
            serde_json::json!({"spend": format!("{:04}-{:02}-{:02}", d.0, d.1, d.2), "pipeline": 100.0 + i as f64})
        });
        json["rows"] = serde_json::Value::Array(rows.collect());
        let svg = render_json(json);
        let text = regex::Regex::new(r">([^<>]+)</text>").expect("pattern");
        text.captures_iter(&svg).map(|c| c[1].to_string()).collect()
    }

    /// On weekly ticks the first and last data points are named when they keep room beside the outermost tick's
    /// label; the first carries the context row its tick hands over.
    #[test]
    fn a_week_ticked_time_axis_labels_a_first_and_last_point_with_room_beside_their_neighbor() {
        let labels = daily_scatter_labels(76, None);
        assert_eq!(labels.first().map(String::as_str), Some("3"), "{labels:?}");
        assert!(labels.iter().any(|l| l == "Mar'26"), "{labels:?}");
        assert!(labels.iter().any(|l| l == "17"), "{labels:?}");
    }

    /// A month label over a domain inside one month has no boundary to center between: weekly labels stay.
    #[test]
    fn a_month_label_over_a_one_month_scatter_domain_falls_back_to_week_ticks() {
        let labels = daily_scatter_labels(23, Some("yearmonth"));
        assert!(labels.iter().any(|l| l == "16"), "weekly day labels: {labels:?}");
        assert!(!labels.iter().any(|l| l == "Mar"), "no single month label: {labels:?}");
    }

    /// A year-shaped x (2019-2023) is a time axis, as on line: the plot spans the years, never
    /// zero, and no tick carries a thousands comma. A non-year numeric x keeps its zero join.
    #[test]
    fn a_year_shaped_x_draws_a_year_axis_not_a_zero_joined_number_line() {
        let _locale = crate::time_locale::en_us_scope();
        let tick_labels = |svg: &str| -> Vec<String> {
            svg.split("<text").skip(1).filter_map(|t| t.split('>').nth(1)?.split('<').next().map(str::to_string)).collect()
        };
        let mut years = make_scatter_payload(false);
        years["resolved"]["style"]["label_usable_ratio"] = serde_json::json!(0.85);
        for (row, year) in years["rows"].as_array_mut().expect("rows").iter_mut().zip([2019, 2021, 2023]) {
            row["spend"] = serde_json::json!(year);
        }
        let labels = tick_labels(&render_json(years));
        for year in ["2019", "2021", "2023"] {
            assert!(labels.iter().any(|l| l == year), "{year} in {labels:?}");
        }
        assert!(labels.iter().all(|l| l != "0" && !l.contains(',')), "{labels:?}");

        let plain = tick_labels(&render_json(make_scatter_payload(false)));
        assert!(plain.iter().any(|l| l == "0"), "a non-year x still joins zero: {plain:?}");
    }

    /// Point value labels beside a per-series fold paint one label per folded point.
    #[test]
    fn wide_measures_with_point_labels_paint_one_label_per_point() {
        let mut json = make_wide_dot_band_payload();
        json["resolved"]["style"]["point_mark"]["labels"] = serde_json::json!({"visible": true, "font": {"family": TEST_FONT}});
        let svg = render_json(json);
        for value in ["10", "30", "50", "70"] {
            assert!(svg.contains(&format!(">{value}</text>")), "{value}: {svg}");
        }
    }

    /// A wide fold on a left y axis keeps its joined measure title and the
    /// legend, with the value labels end-anchored left of the plot.
    #[test]
    fn wide_measures_on_a_left_axis_draw_the_fold() {
        let mut json = make_wide_dot_band_payload();
        json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
        let svg = render_json(json);
        assert!(!svg.contains("__wide"), "no synthetic field name leaks: {svg}");
        assert_eq!(symbol_centers(&svg).len(), 8, "6 points and 2 legend swatches: {svg}");
    }

    /// Review round 1 HIGH 4: the title-band fix (`render_fit` reserves NO
    /// space above a title-less legend) has no test that catches its own
    /// reversal — pins both halves the reference SVG carries: the first
    /// swatch's y offset (no 17px title band eaten before entry 0) and the
    /// legend's own box height, `(n-1)*ROW_ADVANCE + ROW_H` for this
    /// payload's 2 entries (`h=24`).
    #[test]
    fn wide_measures_legend_title_band_reserves_no_space() {
        let svg = render_json(make_wide_dot_band_payload());
        let centers = symbol_centers(&svg);
        // 6 data points + 2 legend swatches (`wide_measures_fold_places_
        // two_colored_points_per_nominal_x_band`); the legend's own two
        // paint LAST (after every data point).
        let legend = &centers[centers.len() - 2..];
        // The symbol centers at 6 in its 12px cell (`legend_grid`), and an
        // unstroked symbol's rows pitch 2 + 11.
        assert_eq!(legend[0].1, 6.0, "no title band: {svg}");
        // The box height shares the title band with this entry origin, so
        // the swatch offset is the one observable that pins both.
        assert_eq!(legend[1].1 - legend[0].1, 13.0, "{svg}");
    }

    /// d3's `band()` rescale centers a lone point-scale category at the
    /// plot's midpoint (review round 1 MEDIUM: the port's shortcut formula
    /// dropped d3's `align` term, landing a single category at the left
    /// edge instead — only reachable at `n == 1`, since the two formulas
    /// agree for every `n >= 2` this family's other tests already cover).
    #[test]
    fn a_single_category_point_scale_centers_the_lone_band() {
        let mut json = make_scatter_payload(false);
        json["resolved"]["x"] = serde_json::json!("team");
        json["resolved"]["style"]["axis_x"]["scale"] = serde_json::json!({"padding": 0.0});
        json["rows"] = serde_json::json!([{"team": "Solo", "pipeline": 140.0}]);
        let svg = render_json(json);
        let (plot_w, _) = plot_size(&svg);
        let centers = symbol_centers(&svg);
        assert_eq!(centers.len(), 1, "{svg}");
        assert!((centers[0].0 - plot_w / 2.0).abs() < 1e-3, "{centers:?} vs {plot_w}");
    }

    /// `(rotate, dy, anchor, translate x, translate y)` of the first
    /// `<text>` ending in `text` — bar's own `rotated_label` test helper
    /// (`bar.rs`), reused here so both families read a rotated axis label
    /// the same way.
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

    /// `ladder/sizing/25-scatter-nominal-x-tilt-refuses`'s exact shape:
    /// eight realistic department names at a width production's own
    /// `resolve_axis_x_overlap` tilts — this arm reuses bar's own tilt
    /// ladder (`vega_fit::category_label_layout`/`vl_x_label_anchor`)
    /// rather than refusing. At −90° `vl_x_label_anchor` picks
    /// (`"right"`, `"middle"`) — anchor `"end"`, `dy = baseline_shift
    /// ("middle", 11) = 3`; `cos(90°) = 0` makes the rotated ink's own
    /// arithmetic exact with no trig left: `text_ink`'s box is `[cx − w,
    /// row_y − 6] … [cx, row_y + 5]` (`dy_ink = 3 − round(0.8·11) = 3 − 9
    /// = −6`), and rotating a box by 90° about its own corner swaps width
    /// for height, landing `ink.y2 == row_y + w` — so the reserved bottom
    /// margin is `ceil(row_y_offset + w)` where `row_y_offset =
    /// axis.ticks.offset (1.0) + tick_eff (0, ticks hidden) +
    /// labels.padding (6.0) = 7.0` and `w` is "Platform Engineering"'s own
    /// measured width (widest of the eight, 108.652px shaped at 11px Inter) —
    /// `ceil(7.0 + 108.652) == 116`.
    ///
    /// The plot's own LEFT margin comes from the LEFTMOST band's (cx=0)
    /// own rotated ink `x1` — vega-scenegraph's `text.js bound()` has no
    /// expand, so a point-scale x carries none either (unlike bar's band
    /// axis, which bakes its own axis-group `+0.5` crisp translate back
    /// out — `common::rotated_category_label_ink`'s own doc). At exactly
    /// −90° the pre-rotation box's WIDTH (`w`, label-dependent) becomes
    /// the rotated box's NEW Y-extent (the bottom-margin reach above),
    /// while its fixed one-row HEIGHT (`[row_y−6, row_y+5]`, from
    /// align="right"/baseline="middle"'s own `dy`, independent of `w`)
    /// becomes the new X-extent — every −90° label, whatever its own
    /// width, shares the identical `[cx−6, cx+5]` box (at +90°, where
    /// `vl_x_label_anchor` instead picks `"left"`/`"middle"`, the mirrored
    /// box is `[cx−5, cx+6]` — not this rung's own shape). `ceil(6) = 6`,
    /// `pad_l(16) + 6 = 22`, one px narrower than the ±0.5-inflated `23`
    /// this arm drew before the fix, so `plot_w = width(520) − pad_l(16)
    /// − 6 − pad_r(16) − right_margin` — unchanged by this fix, hence the
    /// pinned `457` is the pre-fix `456` plus that one px.
    ///
    /// The x-axis TITLE sits at Vega's own `axisTitleLayout` offset — `s +
    /// titlePadding`, `s` the label ink's own `bounds.y2` past the axis
    /// pivot — read back from the SAME `rotated_category_label_ink` the
    /// margin above uses, at `row_y = cx = 0` so `.y2` is the pure reach:
    /// `ink.y2 == w` at −90° (the same identity the margin derivation
    /// uses), maxed over every label (108.652, "Platform Engineering"
    /// again). `title_y = anchor_y + 108.652 + title.padding(10) +
    /// baseline_shift("top", 11)(9)` — `anchor_y` itself moves once the
    /// title is visible (its own `x_title_extra` reserve grows the bottom
    /// margin, shrinking `plot_h`), so the test derives it from the
    /// render's own negotiated `plot_h` rather than a value read off one
    /// specific run. Both `w` values above are `FontBook::measure_shaped`
    /// (GPOS kerning applied; 108.899 raw): Vega measures this label for
    /// itself, so the shaped pass is the law
    /// (`docs/laws-shared.md#which-measurer-a-width-takes-shaped-vs-raw`).
    #[test]
    fn nominal_x_labels_that_would_tilt_take_the_tilt_ladder() {
        let mut json = make_scatter_payload(false);
        json["resolved"]["x"] = serde_json::json!("department");
        json["resolved"]["style"]["axis_x"]["scale"] = serde_json::json!({"padding": 0.0});
        json["resolved"]["style"]["axis_x"]["labels"]["overlap"] =
            serde_json::json!({"skip": true, "tilt": true});
        json["resolved"]["style"]["axis_x"]["labels"]["tilt_increments"] =
            serde_json::json!([0.0, -30.0, -45.0, -60.0, -90.0]);
        json["resolved"]["style"]["axis_x"]["title"]["visible"] = serde_json::json!(true);
        json["resolved"]["style"]["label_usable_ratio"] = serde_json::json!(0.8);
        json["width"] = serde_json::json!(520.0);
        json["height"] = serde_json::json!(432.0);
        json["padding"] = serde_json::json!({"left": 16.0, "right": 16.0, "top": 16.0, "bottom": 16.0});
        json["rows"] = serde_json::json!([
            {"department": "Customer Success", "pipeline": 140.0},
            {"department": "Platform Engineering", "pipeline": 200.0},
            {"department": "Billing Operations", "pipeline": 250.0},
            {"department": "Product Design", "pipeline": 180.0},
            {"department": "Data Analytics", "pipeline": 220.0},
            {"department": "Field Sales", "pipeline": 160.0},
            {"department": "Legal Compliance", "pipeline": 240.0},
            {"department": "Human Resources", "pipeline": 190.0},
        ]);
        let svg = render_json(json);
        // Marks still paint — proof the fit arm rendered, not a refusal.
        assert_eq!(symbol_centers(&svg).len(), 8, "{svg}");
        // "Billing Operations" sorts first alphabetically (the point-scale
        // domain's own order, no chart `sort` authored) and sits at the
        // leftmost band (padding 0, n=8, band(0) == 0).
        let (rot, dy, anchor, tx, _ty) =
            rotated_label(&svg, "Billing Operations").expect("rotated");
        assert_eq!((rot, dy, anchor.as_str(), tx), (270.0, 3.0, "end", 0.5), "{svg}");
        let (plot_w, plot_h) = plot_size(&svg);
        assert_eq!(plot_w, 457.0, "{svg}");
        // A visible title grows the reserved bottom band beyond the
        // labels' own 116 (above) by `x_title_extra` — `title.padding(10)
        // + title_fs(11) + round(0.79·11)(9) − round(0.8·11)(9) = 21` —
        // `ceil(115.652 + 21) = 137` (`115.652 = row_y_offset(7.0) +
        // 108.652`, the labels' own reach before the `.max(0.0)`/`ceil`
        // this file's fit already applies).
        assert_eq!(432.0 - 16.0 - 16.0 - plot_h, 137.0, "reserved bottom band: {svg}");
        // Title sits BELOW the rotated label column, never at the
        // flat-label row it would overlap ("Field Sales" and "Human
        // Resources" both draw within that column) — `anchor_y` (read
        // off the negotiated `plot_h` above, since a visible title
        // itself changes the fit) plus the same 108.652 reach, plus
        // `title.padding(10) + baseline_shift("top", 11)(9)`.
        let title_tag = {
            let end = svg.find(">Department<").unwrap();
            let start = svg[..end].rfind("<text").unwrap();
            &svg[start..end]
        };
        let title_y = attr(title_tag, "y");
        // anchor_y = plot_h + x_axis_offset(1.0) + the axis group's +0.5
        // crisp translate + tick_eff(0.0, hidden) + labels.padding(6.0).
        let (x_axis_offset, crisp, tick_eff, label_pad) = (1.0, 0.5, 0.0, 6.0);
        let anchor_y = plot_h + x_axis_offset + crisp + tick_eff + label_pad;
        let (title_padding, title_baseline_shift) = (10.0, 9.0);
        let shaped = test_fonts().measure_shaped("Platform Engineering", TEST_FONT, 11.0).unwrap();
        assert!((shaped - 108.6519).abs() < 1e-3, "kerned width: {shaped}");
        let expected = anchor_y + shaped + title_padding + title_baseline_shift;
        assert!((title_y - expected).abs() < 0.01, "title y: {title_y} vs {expected} in {svg}");
    }

    /// A rotated nominal-x label's reach is its width as Vega measures it,
    /// kerning applied (`measure_shaped`):
    /// `docs/laws-shared.md#which-measurer-a-width-takes-shaped-vs-raw`. At
    /// the authored −90° the reserved bottom band is `ceil(row_y_offset(7.0)
    /// + widest label + x_title_extra(21))` and the title sits `widest`
    /// below the pivot; "To Tow Try" is 60.0059 raw but 57.6372 shaped, so
    /// the band is 86 where the raw advance measures 89.
    #[test]
    fn nominal_x_rotated_label_reach_takes_the_shaped_width() {
        let fonts = test_fonts();
        let labels = ["To Tow Try", "AV Travel", "LAVA Tray"];
        let widest = labels
            .iter()
            .map(|l| fonts.measure_shaped(l, TEST_FONT, 11.0).unwrap())
            .fold(0.0_f64, f64::max);
        assert_eq!((7.0 + widest + 21.0).ceil(), 86.0, "fixture: shaped and raw bands differ");
        let mut json = make_scatter_payload(false);
        json["resolved"]["x"] = serde_json::json!("team");
        json["resolved"]["style"]["axis_x"]["scale"] = serde_json::json!({"padding": 0.0});
        json["resolved"]["style"]["axis_x"]["labels"]["angle"] = serde_json::json!(-90.0);
        json["resolved"]["style"]["axis_x"]["title"]["visible"] = serde_json::json!(true);
        json["rows"] = serde_json::json!(
            labels.iter().map(|l| serde_json::json!({"team": l, "pipeline": 140.0})).collect::<Vec<_>>()
        );
        let svg = render_json(json);
        assert_eq!(symbol_centers(&svg).len(), 3, "{svg}");
        let (_, plot_h) = plot_size(&svg);
        assert_eq!(432.0 - 32.0 - plot_h, 86.0, "reserved bottom band: {svg}");
        let title_tag = {
            let end = svg.find(">Team<").unwrap();
            let start = svg[..end].rfind("<text").unwrap();
            &svg[start..end]
        };
        let anchor_y = plot_h + 1.0 + 0.5 + 0.0 + 6.0;
        let expected = anchor_y + widest + 10.0 + 9.0;
        let title_y = attr(title_tag, "y");
        assert!((title_y - expected).abs() < 0.01, "title y: {title_y} vs {expected} in {svg}");
    }

    /// `resolve_axis_x_overlap` (`emitters/_label_overlap.py:718-731`)
    /// short-circuits on an AUTHORED `labels.angle` before the crowding
    /// ladder ever runs — bar's own category axis reads it the same way
    /// (`bar.rs`'s `cat_angle` match, `Some(angle) => angle` first) — so a
    /// payload with no `labels.overlap`/`tilt_increments` at all still
    /// rotates once `labels.angle` is set. At −45° (normalized 315°)
    /// `vl_x_label_anchor` picks (`"right"`, `"top"`) — anchor `"end"`,
    /// `dy = baseline_shift("top", 11) = round(0.79·11) = 9`. The reserved
    /// bottom band has a closed form: `text_ink`'s box for
    /// align="right"/baseline="top" is `[cx−w, row_y] … [cx, row_y+11]`
    /// (`dy_ink = 9 − round(0.8·11) = 0`), and rotating it 315° about
    /// `(cx, row_y)` puts the deepest corner at
    /// `row_y + sin(45°)·(w + 11)` (the `(cx−w, row_y+11)` corner) —
    /// `ceil(7 + sin(45°)·(w + 11)) == 41` for "Charlie" (the widest
    /// of the three) with the title hidden; the visible title below adds
    /// `x_title_extra` on top (`10 + 11 + 9 − 9 = 21`), landing
    /// `ceil(7 + sin(45°)·(w + 11) + 21) == 62`. `tx_c` drops by the same one px the
    /// tilt test's `plot_w` gains: "Alpha" (leftmost, cx=0) has `ink.x1 =
    /// cx − cos(45°)·w`, no ±0.5 expand on this point-scale axis, so
    /// `left_margin = pad_l(16) + ceil(cos(45°)·w) = 16+21 = 37` and
    /// `plot_w = width(600) − 37 − pad_r(16) = 547`, one wider than the
    /// pre-fix 546 — `tx_c = 522.5`. The title itself sits at `anchor_y +
    /// sin(45°)·(w+11) + title.padding(10) + baseline_shift("top",
    /// 11)(9)`, `anchor_y` read off the render's own negotiated `plot_h`
    /// the same way as the tilt test above (the title's own reserve moves
    /// it too); `w` (both "Charlie"'s and "Alpha"'s) is `FontBook::
    /// measure_shaped`, the width Vega measures the label at.
    #[test]
    fn nominal_x_authored_angle_is_drawn_rotated() {
        let mut json = make_scatter_payload(false);
        json["resolved"]["x"] = serde_json::json!("team");
        json["resolved"]["style"]["axis_x"]["scale"] = serde_json::json!({"padding": 0.0});
        json["resolved"]["style"]["axis_x"]["labels"]["angle"] = serde_json::json!(-45.0);
        json["resolved"]["style"]["axis_x"]["title"]["visible"] = serde_json::json!(true);
        json["rows"] = serde_json::json!([
            {"team": "Bravo", "pipeline": 140.0},
            {"team": "Alpha", "pipeline": 200.0},
            {"team": "Charlie", "pipeline": 250.0},
        ]);
        let svg = render_json(json);
        assert_eq!(symbol_centers(&svg).len(), 3, "{svg}");
        // No `labels.overlap`/`tilt_increments` authored at all — the
        // authored angle alone drives the rotation, the ladder never runs.
        let (rot, dy, anchor, tx, _ty) = rotated_label(&svg, "Alpha").expect("rotated");
        assert_eq!((rot, dy, anchor.as_str(), tx), (315.0, 9.0, "end", 0.5), "{svg}");
        let (rot_c, _, _, tx_c, _) = rotated_label(&svg, "Charlie").expect("rotated");
        assert_eq!(rot_c, 315.0, "{svg}");
        assert_eq!(tx_c, 522.5, "{svg}");
        let (_, plot_h) = plot_size(&svg);
        // The visible title's own `x_title_extra` (10+11+9-9=21) grows
        // the label-only reserve (41) to 62.
        assert_eq!(432.0 - 32.0 - plot_h, 62.0, "reserved bottom band: {svg}");
        // Title sits below the rotated label column, not at the
        // flat-label row it would overlap — `anchor_y` read off this
        // render's own negotiated `plot_h` (a visible title moves it).
        let title_tag = {
            let end = svg.find(">Team<").unwrap();
            let start = svg[..end].rfind("<text").unwrap();
            &svg[start..end]
        };
        let title_y = attr(title_tag, "y");
        // anchor_y = plot_h + x_axis_offset(1.0) + the axis group's +0.5
        // crisp translate + tick_eff(0.0, hidden) + labels.padding(6.0).
        let (x_axis_offset, crisp, tick_eff, label_pad) = (1.0, 0.5, 0.0, 6.0);
        let anchor_y = plot_h + x_axis_offset + crisp + tick_eff + label_pad;
        let (sin45, _) = std::f64::consts::FRAC_PI_4.sin_cos();
        let (title_padding, title_baseline_shift) = (10.0, 9.0);
        let charlie = test_fonts().measure_shaped("Charlie", TEST_FONT, 11.0).unwrap();
        let expected = anchor_y + sin45 * (charlie + 11.0) + title_padding + title_baseline_shift;
        assert!((title_y - expected).abs() < 0.01, "title y: {title_y} vs {expected} in {svg}");
    }

    /// Vega's domain sort is `{op: min}` over the sort field, which JS
    /// compares as text when the key is text: here the category itself, so
    /// `desc` reverses the code-unit order.
    #[test]
    fn a_dot_plot_sorted_by_a_text_field_ranks_by_its_code_units() {
        let mut json = make_dot_plot_payload(dot_rows("Charlie"));
        json["resolved"]["sort"] = serde_json::json!({"by": "team", "order": "desc"});
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert_eq!(band_labels(&svg), ["Charlie", "Bravo", "Alpha"], "{svg}");
    }
    /// The category texts of the drawn bands, top to bottom.
    fn band_labels(svg: &str) -> Vec<String> {
        texts(svg)
            .into_iter()
            .filter(|t| t.2 == "start" && t.3.chars().next().is_some_and(char::is_alphabetic))
            .map(|t| t.3)
            .collect()
    }

    /// With no authored `sort`, Vega's `sort: true` orders the domain
    /// ascending on the category value itself — a JS code-unit compare, so
    /// every uppercase name precedes a lowercase one (probed: rows
    /// `bravo, Alpha, Charlie` render `Alpha, Charlie, bravo`).
    #[test]
    fn an_unsorted_domain_is_code_unit_ascending() {
        let mut json = make_dot_plot_payload(dot_rows("Charlie"));
        json["resolved"]["sort"] = serde_json::Value::Null;
        json["rows"] = serde_json::json!([
            {"median_response_hours": 3.0, "team": "bravo"},
            {"median_response_hours": 1.0, "team": "Alpha"},
            {"median_response_hours": 2.0, "team": "Charlie"},
        ]);
        let svg = render_json(json);
        assert_eq!(band_labels(&svg), ["Alpha", "Charlie", "bravo"], "{svg}");
    }

    /// The two domains read different data: an authored sort aggregates the
    /// UNFILTERED rows (`source_0`), so a category whose only row has no
    /// measure still gets a band; `sort: true` reads the MARK rows
    /// (`data_0`), which that row never reaches.
    #[test]
    fn only_a_sorted_domain_keeps_a_measureless_category() {
        let rows = serde_json::json!([
            {"median_response_hours": 5.0, "team": "Hi"},
            {"median_response_hours": 2.0, "team": "Mid"},
            {"median_response_hours": null, "team": "NoValue"},
        ]);
        let sorted = render_json(make_dot_plot_payload(rows.clone()));
        assert_eq!(band_labels(&sorted), ["Hi", "Mid", "NoValue"], "{sorted}");
        let mut json = make_dot_plot_payload(rows);
        json["resolved"]["sort"] = serde_json::Value::Null;
        let unsorted = render_json(json);
        assert_eq!(band_labels(&unsorted), ["Hi", "Mid"], "{unsorted}");
    }

    /// Vega's `min` aggregate ignores nulls, and its comparator puts a
    /// category with no value below every number: last descending, first
    /// ascending (probed on both directions).
    #[test]
    fn a_category_with_no_sort_value_sorts_below_every_number() {
        let rows = serde_json::json!([
            {"median_response_hours": 5.0, "team": "Hi"},
            {"median_response_hours": 2.0, "team": "Mid"},
            {"median_response_hours": null, "team": "NoValue"},
        ]);
        let desc = render_json(make_dot_plot_payload(rows.clone()));
        assert_eq!(band_labels(&desc), ["Hi", "Mid", "NoValue"], "{desc}");
        let mut json = make_dot_plot_payload(rows);
        json["resolved"]["sort"]["order"] = serde_json::json!("asc");
        let asc = render_json(json);
        assert_eq!(band_labels(&asc), ["NoValue", "Mid", "Hi"], "{asc}");
    }

    /// The domain sort is stable, so categories tied on the sort measure
    /// keep first-appearance order in both directions.
    #[test]
    fn tied_categories_keep_first_appearance_order() {
        let rows = serde_json::json!([
            {"median_response_hours": 2.0, "team": "Bravo"},
            {"median_response_hours": 1.0, "team": "Alpha"},
            {"median_response_hours": 2.0, "team": "Charlie"},
        ]);
        let desc = render_json(make_dot_plot_payload(rows.clone()));
        assert_eq!(band_labels(&desc), ["Bravo", "Charlie", "Alpha"], "{desc}");
        let mut json = make_dot_plot_payload(rows);
        json["resolved"]["sort"]["order"] = serde_json::json!("asc");
        let asc = render_json(json);
        assert_eq!(band_labels(&asc), ["Alpha", "Bravo", "Charlie"], "{asc}");
    }

    /// The legacy arm a title-overflow scatter falls back to draws no
    /// categorical marks at all, so a dot plot instead takes the fit arm's
    /// own wrap-two law (`common::wrap_title_block`/`emit_title_block_vega`,
    /// the same shared law line/area's fit arm already uses) rather than
    /// refusing or rendering the empty plot the old legacy fallback would.
    #[test]
    fn a_dot_plot_with_a_wrapping_title_wraps_instead_of_refusing() {
        let mut json = make_dot_plot_payload(dot_rows("Charlie"));
        json["resolved"]["title"] = serde_json::json!(
            "Median response time by team, by quarter, across every support region \
             and escalation tier we track"
        );
        json["resolved"]["title_style"] = serde_json::json!({
            "font": {"family": TEST_FONT, "size": 18.0, "weight": "500", "color": "#494A4C"},
            "position": {"offset": 10.0}
        });
        let svg = render_json(json);
        // Two lines, one `data-authored-kind="title"` stamp (only the first
        // line carries it — `emit_title_block_vega`'s own "one group, not
        // one per line" law) — never a single clipped/refused row.
        assert!(
            svg.contains(
                "data-authored-kind=\"title\">Median response time by team, \
                 by quarter, across every support</text>"
            ),
            "{svg}"
        );
        assert!(
            svg.contains(">region and escalation tier we track</text>"),
            "{svg}"
        );
        assert_eq!(svg.matches("data-authored-kind=\"title\"").count(), 1, "{svg}");
        // Pin the RESERVATION, not just the drawn text: the second line's y
        // advances by `fontSize + LINE_ADVANCE_EXTRA` past the first
        // (`TitleInk::with_lines`'s own `ink_h` growth) — a regression to a
        // single-line `TitleInk` with the wrapped text merely drawn over the
        // plot would still pass the two assertions above.
        let tag_ending_in = |needle: &str| -> &str {
            let end = svg.find(needle).unwrap() + needle.len();
            let start = svg[..end].rfind("<text").unwrap();
            &svg[start..end]
        };
        let first_line_y = attr(tag_ending_in("across every support</text>"), "y");
        let second_line_y = attr(tag_ending_in("region and escalation tier we track</text>"), "y");
        // 18.0 is the authored `title_style.font.size` above.
        assert_eq!(
            second_line_y - first_line_y,
            18.0 + crate::vega_fit::TitleInk::LINE_ADVANCE_EXTRA,
            "{svg}"
        );
    }

    /// A time `labels.format` on a categorical y routes through a UTC labelExpr
    /// over date bands; over any other value production's `gate_label_format`
    /// raises `ERR-TIME-FORMAT-NOT-TEMPORAL` instead of painting "Invalid Date".
    #[test]
    fn a_time_label_format_on_a_categorical_y_raises_the_gate() {
        let mut json = make_dot_plot_payload(dot_rows("Charlie"));
        json["resolved"]["style"]["axis_y"]["labels"]["format"] = serde_json::json!("%b %Y");
        let err = render_err(json);
        assert!(err.starts_with("ERR-TIME-FORMAT-NOT-TEMPORAL"), "{err}");
        assert!(err.contains("axis_y.labels.format"), "{err}");
        // A number format is dropped by production, not routed — the
        // category strings render as themselves.
        let mut ok = make_dot_plot_payload(dot_rows("Charlie"));
        ok["resolved"]["style"]["axis_y"]["labels"]["format"] = serde_json::json!(".3~s");
        assert_eq!(band_labels(&render_json(ok)), ["Bravo", "Charlie", "Alpha"]);
    }

    /// Date bands pass the gate and every band is relabeled through the format.
    #[test]
    fn a_time_label_format_on_date_bands_relabels_every_band() {
        let _locale = crate::time_locale::en_us_scope();
        let mut json = make_dot_plot_payload(serde_json::json!([
            {"median_response_hours": 3.0, "team": "2024-02"},
            {"median_response_hours": 1.0, "team": "2024-01"},
            {"median_response_hours": 2.0, "team": "2024-03-15"},
        ]));
        json["resolved"]["style"]["axis_y"]["labels"]["format"] = serde_json::json!("%b %Y");
        assert_eq!(band_labels(&render_json(json)), ["Feb 2024", "Mar 2024", "Jan 2024"]);
    }

    // ── Zero rule (#8241's scatter half) ──────────────────────────────────

    const ZERO_RULE_COLOR: &str = "#123456";

    fn set_zero_style(json: &mut serde_json::Value, grid_visible: bool) {
        json["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(grid_visible);
        json["resolved"]["style"]["axis_y"]["grid"]["threshold"] =
            serde_json::json!({"color": ZERO_RULE_COLOR, "width": 2.0});
    }

    fn zero_rule_tag(svg: &str) -> Option<&str> {
        svg.split("<line ")
            .skip(1)
            .find(|t| t[..t.find('>').unwrap_or(t.len())].contains(ZERO_RULE_COLOR))
    }

    /// `non_bar_zero_rule_should_fire`: with no authored domain, a scatter
    /// whose resolved `axis_y.zero_anchored` is true draws the rule at the
    /// exact scale position for datum 0 — here the domain floor, so the
    /// rule sits at the plot's bottom edge.
    #[test]
    fn zero_rule_fires_for_a_zero_anchored_quantitative_scatter() {
        let mut json = make_scatter_payload(false);
        set_zero_style(&mut json, true);
        json["resolved"]["style"]["axis_y"]["zero_anchored"] = serde_json::json!(true);
        json["resolved"]["style"]["axis_y"]["domain_min"] = serde_json::json!(0.0);
        json["resolved"]["style"]["axis_y"]["domain_max"] = serde_json::json!(250.0);
        let svg = render_json(json);
        let (plot_w, plot_h) = plot_size(&svg);
        let tag = zero_rule_tag(&svg).unwrap_or_else(|| panic!("zero rule drawn: {svg}"));
        assert!(attr(tag, "x2") >= plot_w && attr(tag, "x1") <= 0.0, "spans full plot width: {tag}");
        assert!((attr(tag, "y1") - plot_h).abs() < 1e-6, "datum 0 at the domain floor: {tag}");
        assert!((attr(tag, "y2") - plot_h).abs() < 1e-6, "{tag}");
        // `_insert_rule` routes a scatter's rule to `spec.underlays`
        // (#8343) — it paints BENEATH the points, not on top of them.
        // Pin the z-order itself, not just the rule's presence: a mutant
        // that moves the emission below the point loop leaves every other
        // assertion here green.
        let rule_at = svg.find(ZERO_RULE_COLOR).expect("rule drawn");
        let first_point = svg.find("<path transform=\"translate(").expect("points drawn");
        assert!(rule_at < first_point, "the rule paints beneath the points: {svg}");
    }

    /// `_y_carries_the_measure`: a rotated scatter (dot plot — categorical
    /// y, `is_quantitative: false` on the resolved axis) never draws the
    /// rule, even with `zero_anchored` and a zero grid style both set —
    /// there is no position for `datum: 0` on a categorical axis.
    #[test]
    fn zero_rule_does_not_fire_on_a_rotated_dot_plot() {
        let mut json = make_dot_plot_payload(dot_rows("Charlie"));
        set_zero_style(&mut json, true);
        json["resolved"]["style"]["axis_y"]["zero_anchored"] = serde_json::json!(true);
        let svg = render_json(json);
        assert!(zero_rule_tag(&svg).is_none(), "{svg}");
    }

    /// `_y_carries_the_measure` reads the RESOLVED `is_quantitative` flag,
    /// never this file's own `y_nominal` channel-kind heuristic — the two
    /// agree on every swept case, but production's law names the field, not
    /// the port's own re-derivation. A measure-shaped scatter (all-numeric
    /// y, so the port's own heuristic would enter the measure branch) with
    /// `is_quantitative: false` still refuses the rule.
    #[test]
    fn zero_rule_reads_is_quantitative_not_the_ports_own_y_nominal_guess() {
        let mut json = make_scatter_payload(false);
        set_zero_style(&mut json, true);
        json["resolved"]["style"]["axis_y"]["zero_anchored"] = serde_json::json!(true);
        json["resolved"]["style"]["axis_y"]["is_quantitative"] = serde_json::json!(false);
        let svg = render_json(json);
        assert!(zero_rule_tag(&svg).is_none(), "{svg}");
    }

    /// Regression (#8343 generalize): `grid.threshold.visible` is an
    /// independent off-switch from `grid.visible` — production silences the
    /// rule without hiding this axis's other gridlines.
    #[test]
    fn zero_rule_suppressed_by_threshold_visible_false_even_with_grid_visible() {
        let mut json = make_scatter_payload(false);
        json["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(true);
        json["resolved"]["style"]["axis_y"]["grid"]["threshold"] =
            serde_json::json!({"visible": false, "color": ZERO_RULE_COLOR, "width": 2.0});
        json["resolved"]["style"]["axis_y"]["zero_anchored"] = serde_json::json!(true);
        let svg = render_json(json);
        assert!(zero_rule_tag(&svg).is_none(), "{svg}");
    }

    /// `build_zero_rule_if_applicable`: an AUTHORED `scale.continuous.domain`
    /// excluding 0 suppresses the rule outright, even with `zero_anchored`
    /// true — the domain has no legal position for the datum.
    #[test]
    fn zero_rule_suppressed_by_an_authored_domain_excluding_zero() {
        let mut json = make_scatter_payload(false);
        set_zero_style(&mut json, true);
        json["resolved"]["style"]["axis_y"]["zero_anchored"] = serde_json::json!(true);
        json["resolved"]["style"]["axis_y"]["domain_min"] = serde_json::json!(50.0);
        json["resolved"]["style"]["axis_y"]["domain_max"] = serde_json::json!(300.0);
        json["resolved"]["style"]["axis_y"]["scale"] =
            serde_json::json!({"continuous": {"domain": [50.0, 300.0]}});
        let svg = render_json(json);
        assert!(zero_rule_tag(&svg).is_none(), "{svg}");
    }

    /// `non_bar_zero_rule_should_fire`'s `zero_setting is False` branch: an
    /// explicit `scale.continuous.zero: false` requires the shared domain
    /// (here just the base y column, since scatter carries no `layers`) to
    /// straddle 0 — `zero_anchored` plays no part once zero is explicitly
    /// off.
    #[test]
    fn zero_rule_with_explicit_zero_false_requires_the_data_to_straddle() {
        let payload = |straddles: bool| {
            let mut json = make_scatter_payload(false);
            json["rows"] = if straddles {
                serde_json::json!([
                    {"spend": 18.0, "pipeline": -20.0},
                    {"spend": 27.0, "pipeline": 40.0},
                ])
            } else {
                serde_json::json!([
                    {"spend": 18.0, "pipeline": 20.0},
                    {"spend": 27.0, "pipeline": 40.0},
                ])
            };
            json["resolved"]["style"]["axis_y"]["domain_min"] =
                serde_json::json!(if straddles { -25.0 } else { 15.0 });
            json["resolved"]["style"]["axis_y"]["domain_max"] = serde_json::json!(45.0);
            // `use_vega_fit`'s `y_measure` requires a non-empty baked ladder
            // to stay on the fit arm — an empty one falls back to the
            // legacy arm, which this test isn't exercising.
            json["resolved"]["style"]["axis_y"]["tick_values"] = serde_json::json!(
                if straddles { [-20.0, 0.0, 20.0, 40.0] } else { [15.0, 25.0, 35.0, 45.0] }
            );
            json["resolved"]["style"]["axis_y"]["scale"] =
                serde_json::json!({"continuous": {"zero": false}});
            set_zero_style(&mut json, true);
            json
        };
        let straddling = render_json(payload(true));
        let one_sided = render_json(payload(false));
        assert!(zero_rule_tag(&straddling).is_some(), "{straddling}");
        assert!(zero_rule_tag(&one_sided).is_none(), "{one_sided}");
    }

    /// `scatter.rs`'s fit arm must union the rule's own (stroke-expanded)
    /// bounds, the way `line.rs`'s ink pass does — an all-negative measure
    /// axis puts datum 0 entirely above the plot, where nothing else has
    /// ink, so the rule alone forces extra top margin. Toggling only
    /// `grid.threshold` (present/absent) isolates that from every other input.
    #[test]
    fn zero_rule_ink_joins_the_fit_union() {
        let payload = |rule_present: bool| {
            let mut json = make_scatter_payload(false);
            json["rows"] = serde_json::json!([
                {"spend": 18.0, "pipeline": -80.0},
                {"spend": 27.0, "pipeline": -40.0},
                {"spend": 39.0, "pipeline": -10.0},
            ]);
            json["resolved"]["style"]["axis_y"]["domain_min"] = serde_json::json!(-100.0);
            json["resolved"]["style"]["axis_y"]["domain_max"] = serde_json::json!(-5.0);
            json["resolved"]["style"]["axis_y"]["tick_values"] =
                serde_json::json!([-90.0, -50.0, -10.0]);
            json["resolved"]["style"]["axis_y"]["zero_anchored"] = serde_json::json!(true);
            json["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(true);
            json["resolved"]["style"]["axis_y"]["grid"]["threshold"] = if rule_present {
                serde_json::json!({"color": ZERO_RULE_COLOR, "width": 2.0})
            } else {
                serde_json::Value::Null
            };
            json
        };
        let with_rule = render_json(payload(true));
        let without_rule = render_json(payload(false));
        assert!(zero_rule_tag(&with_rule).is_some(), "{with_rule}");
        assert!(zero_rule_tag(&without_rule).is_none(), "{without_rule}");
        let (_, ph_with) = plot_size(&with_rule);
        let (_, ph_without) = plot_size(&without_rule);
        assert!(
            ph_with < ph_without,
            "the off-plot rule's ink should reserve extra top margin: \
             with={ph_with} without={ph_without}"
        );
    }

    // ── BaselineFeature's unity rule, scatter's other half ────────────────

    const UNITY_RULE_COLOR: &str = "#7799AA";

    fn unity_rule_tag(svg: &str) -> Option<&str> {
        svg.split("<line ").skip(1).find(|t| t.contains(UNITY_RULE_COLOR))
    }

    /// A fit-shaped scatter payload whose measure y is percent-formatted
    /// with a pinned domain reaching 1.0 — production's `_apply_unity` gate
    /// (`baseline.py`). Same domain/tick/row numbers as `line.rs`'s own
    /// `percent_payload` fixture, so the two families' unity tests read off
    /// identical gate math.
    fn unity_payload(mutate: impl FnOnce(&mut serde_json::Value)) -> serde_json::Value {
        let mut json = make_scatter_payload(false);
        json["rows"] = serde_json::json!([
            {"spend": 18.0, "pipeline": 0.42},
            {"spend": 27.0, "pipeline": 1.0},
            {"spend": 39.0, "pipeline": 0.67},
        ]);
        let ay = &mut json["resolved"]["style"]["axis_y"];
        ay["labels"]["format"] = serde_json::json!(".0%");
        ay["grid"]["visible"] = serde_json::json!(true);
        ay["grid"]["threshold"] = serde_json::json!({"color": UNITY_RULE_COLOR, "width": 2.0});
        ay["tick_values"] = serde_json::json!([0.2, 0.4, 0.6, 0.8, 1.0, 1.2]);
        ay["domain_min"] = serde_json::json!(0.2224);
        ay["domain_max"] = serde_json::json!(1.0576);
        mutate(&mut json);
        json
    }

    /// Production's `_apply_unity` fires wherever a percent-formatted
    /// measure axis with a visible grid has a pinned domain reaching 1.0 —
    /// same gate `line.rs` ports as `unity_rule_fires`, reused verbatim via
    /// `common::unity_rule_fires`. `_insert_rule` routes a scatter's rule to
    /// `spec.underlays` (#8343), same as the zero rule.
    #[test]
    fn unity_rule_fires_for_a_percent_axis_whose_pinned_domain_reaches_one() {
        let svg = render_json(unity_payload(|_| {}));
        let tag = unity_rule_tag(&svg).unwrap_or_else(|| panic!("unity rule drawn: {svg}"));
        let (plot_w, _) = plot_size(&svg);
        assert!(attr(tag, "x1") <= 0.0 && attr(tag, "x2") >= plot_w, "spans the full plot width: {tag}");
        let rule_at = svg.find(UNITY_RULE_COLOR).expect("rule drawn");
        let first_point = svg.find("<path transform=\"translate(").expect("points drawn");
        assert!(rule_at < first_point, "the unity rule paints beneath the points: {svg}");
    }

    #[test]
    fn unity_rule_does_not_fire_when_the_pinned_domain_stops_short_of_one() {
        let svg = render_json(unity_payload(|p| {
            p["rows"] = serde_json::json!([
                {"spend": 18.0, "pipeline": 0.42},
                {"spend": 27.0, "pipeline": 0.58},
                {"spend": 39.0, "pipeline": 0.51},
            ]);
            p["resolved"]["style"]["axis_y"]["tick_values"] = serde_json::json!([0.2, 0.4, 0.6, 0.8]);
            p["resolved"]["style"]["axis_y"]["domain_max"] = serde_json::json!(0.9);
        }));
        assert!(unity_rule_tag(&svg).is_none(), "nothing the axis pins reaches 1.0: {svg}");
    }

    #[test]
    fn unity_rule_does_not_fire_when_the_grid_is_hidden() {
        let svg = render_json(unity_payload(|p| {
            p["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(false);
        }));
        assert!(unity_rule_tag(&svg).is_none(), "{svg}");
    }

    /// The percent gate reads `axis_y.labels.format` alone
    /// (`_resolved_percent_format(axis)`, `baseline.py:119`): a chart carries
    /// no format of its own to fall back on.
    #[test]
    fn unity_rule_reads_the_axis_format_alone() {
        let bare = render_json(unity_payload(|p| {
            p["resolved"]["style"]["axis_y"]["labels"]["format"] = serde_json::Value::Null;
        }));
        assert!(
            unity_rule_tag(&bare).is_none(),
            "the axis does not format as a percent: {bare}"
        );
        let stray = render_json(unity_payload(|p| {
            p["resolved"]["style"]["axis_y"]["labels"]["format"] = serde_json::Value::Null;
            p["resolved"]["format"] = serde_json::json!(".0%");
        }));
        assert!(unity_rule_tag(&stray).is_none(), "{stray}");
    }

    /// Mirrors line.rs's own gate-precedence test: the gate reads the
    /// PINNED domain edges, never the tick ladder — a rung is a tick
    /// position, not a domain edge.
    #[test]
    fn unity_rule_reads_the_pinned_domain_edges_never_the_tick_ladder() {
        let ladder_over = render_json(unity_payload(|p| {
            p["resolved"]["style"]["axis_y"]["tick_values"] =
                serde_json::json!([0.2, 0.4, 0.6, 0.8, 1.0]);
            p["resolved"]["style"]["axis_y"]["domain_max"] = serde_json::json!(0.9785);
        }));
        assert!(
            unity_rule_tag(&ladder_over).is_none(),
            "the ladder's 1.0 rung is not a domain edge: {ladder_over}"
        );
        let ladder_under = render_json(unity_payload(|p| {
            p["resolved"]["style"]["axis_y"]["tick_values"] = serde_json::json!([0.2, 0.4, 0.6, 0.8]);
            p["resolved"]["style"]["axis_y"]["domain_max"] = serde_json::json!(1.05);
        }));
        assert!(
            unity_rule_tag(&ladder_under).is_some(),
            "the pinned domain reaches 1.0 whatever the ladder draws: {ladder_under}"
        );
    }

    /// `_y_carries_the_measure` (`ax_y.is_quantitative`): a rotated scatter
    /// (dot plot — categorical y) never draws the unity rule, even with a
    /// percent-formatted axis and a visible zero-styled grid — there is no
    /// position for `datum: 1` on a categorical axis.
    #[test]
    fn unity_rule_does_not_fire_on_a_rotated_dot_plot() {
        let mut json = make_dot_plot_payload(dot_rows("Charlie"));
        json["resolved"]["style"]["axis_y"]["labels"]["format"] = serde_json::json!(".0%");
        json["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(true);
        json["resolved"]["style"]["axis_y"]["grid"]["threshold"] =
            serde_json::json!({"color": UNITY_RULE_COLOR, "width": 2.0});
        let svg = render_json(json);
        assert!(unity_rule_tag(&svg).is_none(), "{svg}");
    }

    /// `_y_carries_the_measure` reads the RESOLVED `is_quantitative` flag,
    /// never this file's own `y_nominal` channel-kind heuristic — mirrors
    /// `zero_rule_reads_is_quantitative_not_the_ports_own_y_nominal_guess`.
    /// A measure-shaped scatter (all-numeric y, so the port's own heuristic
    /// enters the measure branch) with `is_quantitative: false` still
    /// refuses the unity rule.
    #[test]
    fn unity_rule_reads_is_quantitative_not_the_ports_own_y_nominal_guess() {
        let svg = render_json(unity_payload(|p| {
            p["resolved"]["style"]["axis_y"]["is_quantitative"] = serde_json::json!(false);
        }));
        assert!(unity_rule_tag(&svg).is_none(), "{svg}");
    }

    /// The rule's own (stroke-expanded) bounds must join `render_fit`'s
    /// fit-ink union, the way `line.rs`'s ink pass and scatter's own zero
    /// rule do — an exaggerated stroke width makes the effect unmistakable
    /// regardless of where the datum lands relative to other ink.
    #[test]
    fn unity_rule_ink_joins_the_fit_union() {
        let payload = |rule_present: bool| {
            unity_payload(|p| {
                p["resolved"]["style"]["axis_y"]["grid"]["threshold"] = if rule_present {
                    serde_json::json!({"color": UNITY_RULE_COLOR, "width": 100.0})
                } else {
                    serde_json::Value::Null
                };
            })
        };
        let with_rule = render_json(payload(true));
        let without_rule = render_json(payload(false));
        assert!(unity_rule_tag(&with_rule).is_some(), "{with_rule}");
        assert!(unity_rule_tag(&without_rule).is_none(), "{without_rule}");
        let (_, ph_with) = plot_size(&with_rule);
        let (_, ph_without) = plot_size(&without_rule);
        assert!(
            ph_with < ph_without,
            "the rule's stroke-expanded ink should reserve extra margin: \
             with={ph_with} without={ph_without}"
        );
    }

    /// `apply()` calls `_apply_zero_or_top` before `_apply_unity`, and
    /// `_insert_rule` routes BOTH scatter rules to `spec.underlays` — so
    /// when the data straddles 0 as well as reaching 1.0, the unity rule
    /// paints after the zero rule (and both beneath the points). Production
    /// styles both rules from the SAME `axis_y.grid.threshold`, so they
    /// share one color, and a count-only assertion cannot tell the two
    /// apart — swapping the emission order would leave every prior
    /// assertion here green, since both rules are the same color and
    /// neither position was checked against the other. The two rules sit
    /// at distinct `y1`s (datum 0's fraction is LARGER
    /// than datum 1's on this domain, since `frac = (y_max - datum) /
    /// y_span`), so pin which rule is which by its own `y1`, not just how
    /// many draw.
    #[test]
    fn unity_rule_paints_after_the_zero_rule_when_both_fire() {
        let mut json = unity_payload(|_| {});
        json["rows"] = serde_json::json!([
            {"spend": 18.0, "pipeline": -0.2},
            {"spend": 27.0, "pipeline": 0.5},
            {"spend": 39.0, "pipeline": 1.0},
        ]);
        let ay = &mut json["resolved"]["style"]["axis_y"];
        ay["domain_min"] = serde_json::json!(-0.3);
        ay["domain_max"] = serde_json::json!(1.05);
        ay["tick_values"] = serde_json::json!([-0.2, 0.0, 0.2, 0.4, 0.6, 0.8, 1.0]);
        let svg = render_json(json);
        let (_, plot_h) = plot_size(&svg);

        let rule_tags: Vec<&str> = svg
            .split("<line ")
            .skip(1)
            .filter(|t| t.contains(UNITY_RULE_COLOR))
            .map(|t| t.split("/>").next().unwrap())
            .collect();
        assert_eq!(rule_tags.len(), 2, "both the zero and unity rules fire: {svg}");
        let first_point = svg.find("<path transform=\"translate(").expect("points drawn");
        assert!(
            svg.find(rule_tags[0]).unwrap() < first_point
                && svg.find(rule_tags[1]).unwrap() < first_point,
            "both rules paint beneath the points: {svg}"
        );

        let y_span = 1.05 - (-0.3);
        let zero_y = plot_h * (1.05 - 0.0) / y_span;
        let unity_y = plot_h * (1.05 - 1.0) / y_span;
        assert!(
            (attr(rule_tags[0], "y1") - zero_y).abs() < 1e-3,
            "the FIRST rule drawn is the zero rule, at y {zero_y}: {rule_tags:?}"
        );
        assert!(
            (attr(rule_tags[1], "y1") - unity_y).abs() < 1e-3,
            "the SECOND rule drawn is the unity rule, at y {unity_y}: {rule_tags:?}"
        );
    }

    // ── Front-end contract: baseline-rule mark-group ids ──────────────────

    /// `translate.py`'s `_translate_standard` composes `[*under_layers,
    /// main_layer, *rule_layers]` — a scatter's single fired rule is the
    /// sole underlay, so it lands at `layer_0`, and the points (the main
    /// layer) land at `layer_1`. The port used to stamp `layer_1_marks` on
    /// the rule and a bare `marks` on the points, so the rule wore the id
    /// that belongs to the points.
    #[test]
    fn single_baseline_rule_is_layer_0_and_points_are_layer_1() {
        let mut json = unity_payload(|_| {});
        json["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.2~f");
        let svg = render_json(json);
        assert!(
            svg.contains("<g class=\"mark-rule role-mark layer_0_marks\""),
            "the sole fired rule is layer_0: {svg}"
        );
        assert!(
            svg.contains("<g class=\"mark-symbol role-mark layer_1_marks\""),
            "the points follow the rule at layer_1: {svg}"
        );
    }

    /// Same law with both rules firing: `spec.underlays` holds `[zero,
    /// unity]` in that order (`_apply_zero_or_top` before `_apply_unity`),
    /// so the points fall out at `layer_2`.
    #[test]
    fn both_baseline_rules_are_layer_0_and_layer_1_and_points_are_layer_2() {
        let mut json = unity_payload(|_| {});
        json["rows"] = serde_json::json!([
            {"spend": 18.0, "pipeline": -0.2},
            {"spend": 27.0, "pipeline": 0.5},
            {"spend": 39.0, "pipeline": 1.0},
        ]);
        let ay = &mut json["resolved"]["style"]["axis_y"];
        ay["domain_min"] = serde_json::json!(-0.3);
        ay["domain_max"] = serde_json::json!(1.05);
        ay["tick_values"] = serde_json::json!([-0.2, 0.0, 0.2, 0.4, 0.6, 0.8, 1.0]);
        json["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.2~f");
        let svg = render_json(json);
        assert!(
            svg.contains("<g class=\"mark-rule role-mark layer_0_marks\""),
            "the zero rule is layer_0: {svg}"
        );
        assert!(
            svg.contains("<g class=\"mark-rule role-mark layer_1_marks\""),
            "the unity rule is layer_1: {svg}"
        );
        assert!(
            svg.contains("<g class=\"mark-symbol role-mark layer_2_marks\""),
            "the points follow both rules at layer_2: {svg}"
        );
    }

    // ── Legacy arm: the same baseline-rule laws, not a fit-arm-only feature ─

    // ── Legacy arm: an unbaked measure axis is Vega's own linear domain ────

    /// A left-axis (legacy-arm) scatter with no baked domain or ladder on
    /// either axis, drawing `rows` of `(x, y)` pairs.
    fn fit_unbaked_payload(rows: &[(f64, f64)]) -> serde_json::Value {
        let mut json = make_scatter_payload(false);
        json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
        json["rows"] = serde_json::Value::Array(
            rows.iter().map(|&(x, y)| serde_json::json!({"spend": x, "pipeline": y})).collect(),
        );
        let ay = &mut json["resolved"]["style"]["axis_y"];
        ay["domain_min"] = serde_json::Value::Null;
        ay["domain_max"] = serde_json::Value::Null;
        ay["tick_values"] = serde_json::json!([]);
        ay["labels"]["align"] = serde_json::Value::Null;
        json
    }

    /// The color scale reads the filtered dataset too (`filterinvalid.ts`
    /// filters the unit's data source ahead of every scale): a series that
    /// appears only on a dropped null-coordinate row keeps no legend entry
    /// and no palette slot. Probed against production with series S, A, B
    /// plus (null, 300, "Z"): the legend lists S, A, B.
    #[test]
    fn fit_arm_legend_omits_a_series_only_on_a_dropped_row() {
        let mut json = fit_unbaked_payload(&[(10.0, 100.0), (20.0, 180.0), (30.0, 220.0)]);
        json["resolved"]["color"] = serde_json::json!("segment");
        json["resolved"]["legend"]["visible"] = serde_json::json!(true);
        let rows = json["rows"].as_array_mut().unwrap();
        for (row, series) in rows.iter_mut().zip(["S", "A", "B"]) {
            row["segment"] = serde_json::json!(series);
        }
        rows.push(serde_json::json!({"spend": null, "pipeline": 300.0, "segment": "Z"}));
        let svg = render_json(json);
        let labels: Vec<&str> = ["A", "B", "S", "Z"]
            .into_iter()
            .filter(|series| svg.contains(&format!(">{series}</text>")))
            .collect();
        assert_eq!(labels, ["A", "B", "S"], "{svg}");
    }

    /// A row the mark filter drops is never "outside the drawn domain": it
    /// paints nothing, so an authored pair narrower than its other
    /// coordinate does not refuse.
    #[test]
    fn fit_arm_null_coordinate_row_never_trips_the_outside_domain_refusal() {
        let mut json = fit_unbaked_payload(&[(10.0, 100.0), (40.0, 300.0)]);
        json["rows"]
            .as_array_mut()
            .unwrap()
            .push(serde_json::json!({"spend": null, "pipeline": 900.0}));
        json["resolved"]["style"]["axis_y"]["scale"] =
            serde_json::json!({"continuous": {"domain": [0.0, 300.0]}});
        let svg = render_json(json);
        assert_eq!(symbol_centers(&svg).len(), 2, "{svg}");
    }

    // ── X-threshold rules (`_apply_x_threshold`) ───────────────────────────

    const X_RULE_COLOR: &str = "#abcdef";

    /// A fit-shaped scatter whose x is percent-formatted and whose data
    /// reaches both 0.0 and 1.0 — production's `_apply_x_threshold`'s two
    /// gates (`baseline.py`). The base fixture's own `axis_y.grid.threshold`
    /// stays `null` (unset by `axis_json`), so the y rules never fire here —
    /// this isolates the new x-side gate from the already-tested y one.
    fn x_threshold_payload(mutate: impl FnOnce(&mut serde_json::Value)) -> serde_json::Value {
        let mut json = make_scatter_payload(false);
        json["rows"] = serde_json::json!([
            {"spend": 0.0, "pipeline": 0.15},
            {"spend": 0.25, "pipeline": 0.40},
            {"spend": 0.50, "pipeline": 0.62},
            {"spend": 0.75, "pipeline": 0.85},
            {"spend": 1.0, "pipeline": 1.0},
        ]);
        json["resolved"]["style"]["axis_x"]["labels"]["format"] = serde_json::json!(".0%");
        json["resolved"]["style"]["axis_x"]["grid"]["visible"] = serde_json::json!(true);
        json["resolved"]["style"]["axis_x"]["grid"]["threshold"] =
            serde_json::json!({"color": X_RULE_COLOR, "width": 2.0});
        let ay = &mut json["resolved"]["style"]["axis_y"];
        ay["domain_min"] = serde_json::json!(0.0);
        ay["domain_max"] = serde_json::json!(1.2);
        ay["tick_values"] = serde_json::json!([0.0, 0.6, 1.2]);
        mutate(&mut json);
        json
    }

    fn x_rule_tags(svg: &str) -> Vec<&str> {
        svg.split("<line ")
            .skip(1)
            .filter(|t| t.contains(X_RULE_COLOR))
            .map(|t| t.split("/>").next().unwrap())
            .collect()
    }

    /// Both `datum: 0` and `datum: 1` fire at their exact scale positions —
    /// the domain floor and ceiling respectively, since the data touches
    /// both edges exactly. Also pins paint order: beneath the points, same
    /// as the y rules (`_insert_rule` routes every scatter rule to
    /// `spec.underlays`).
    #[test]
    fn x_threshold_rules_fire_at_zero_and_one_when_the_x_domain_reaches_both() {
        let svg = render_json(x_threshold_payload(|_| {}));
        let (plot_w, _) = plot_size(&svg);
        let tags = x_rule_tags(&svg);
        assert_eq!(tags.len(), 2, "both x=0 and x=1 rules fire: {svg}");
        assert!(attr(tags[0], "x1").abs() < 1e-2, "datum 0 at the domain floor: {tags:?}");
        assert!(
            (attr(tags[1], "x1") - plot_w).abs() < 1e-2,
            "datum 1 at the domain ceiling: {tags:?}"
        );
        let first_point = svg.find("<path transform=\"translate(").expect("points drawn");
        assert!(
            svg.find(tags[0]).unwrap() < first_point && svg.find(tags[1]).unwrap() < first_point,
            "both x rules paint beneath the points: {svg}"
        );
    }

    /// `require_percent`: the `datum: 1` x rule additionally requires x's
    /// OWN resolved label format to be percent-shaped — a plain number
    /// format still lets `datum: 0` fire (mirrors the corpus's p2/p9 rows,
    /// which draw only the x=0 rule).
    #[test]
    fn x_unity_threshold_requires_a_percent_x_format() {
        let svg = render_json(x_threshold_payload(|p| {
            p["resolved"]["style"]["axis_x"]["labels"]["format"] = serde_json::json!(".3~s");
        }));
        let tags = x_rule_tags(&svg);
        assert_eq!(tags.len(), 1, "only x=0 fires without a percent x format: {svg}");
        assert!(attr(tags[0], "x1").abs() < 1e-2, "{tags:?}");
    }

    /// `_domain_reaches`: neither datum fires when the x domain (here the
    /// raw data extent, since nothing authors an x domain) never reaches
    /// it — independent of the percent-format gate, which stays satisfied
    /// throughout.
    #[test]
    fn x_threshold_does_not_fire_when_the_domain_never_reaches_the_datum() {
        let svg = render_json(x_threshold_payload(|p| {
            p["rows"] = serde_json::json!([
                {"spend": 0.2, "pipeline": 0.3},
                {"spend": 0.5, "pipeline": 0.5},
                {"spend": 0.9, "pipeline": 0.7},
            ]);
        }));
        assert!(
            x_rule_tags(&svg).is_empty(),
            "neither datum lies within the x domain [0.2, 0.9]: {svg}"
        );
    }

    /// `effective_measure_domain`'s branch 3: an authored
    /// `scale.continuous.zero: true` pins the domain floor even when the
    /// data never reaches it — production draws the x=0 rule for
    /// `x: [100, 150, 200]` with `zero: true` authored
    /// (`test_scatter_x_far_from_zero_skips_x_rule`'s own fixture in
    /// `dbt-charts/tests/core/render/chart/test_quantitative_x_baseline.py`,
    /// but WITH `zero: true` added — confirmed live against `_spec`).
    /// `x_scale_untouched` never reads `zero`, so this payload stays on
    /// the fit arm.
    #[test]
    fn x_threshold_zero_anchor_fires_even_when_the_data_never_reaches_zero() {
        let svg = render_json(x_threshold_payload(|p| {
            p["rows"] = serde_json::json!([
                {"spend": 100.0, "pipeline": 0.1},
                {"spend": 150.0, "pipeline": 0.5},
                {"spend": 200.0, "pipeline": 0.9},
            ]);
            p["resolved"]["style"]["axis_x"]["labels"]["format"] = serde_json::json!(".3~s");
            p["resolved"]["style"]["axis_x"]["scale"] =
                serde_json::json!({"continuous": {"zero": true}});
        }));
        let tags = x_rule_tags(&svg);
        assert_eq!(tags.len(), 1, "the zero-anchored floor pins 0 into the domain: {svg}");
        assert!(attr(tags[0], "x1").abs() < 1e-2, "at the domain floor: {tags:?}");
    }

    /// The scale joins BOTH ends with 0 under `zero: true`, so an all-negative
    /// percent x renders with its top edge at 0 — the x=0 rule must fire there,
    /// at the plot's right edge, not be declined by a gate that only lowers lo.
    #[test]
    fn x_threshold_zero_anchor_fires_at_the_top_of_an_all_negative_domain() {
        let svg = render_json(x_threshold_payload(|p| {
            p["rows"] = serde_json::json!([
                {"spend": -0.4, "pipeline": 0.1},
                {"spend": -0.25, "pipeline": 0.5},
                {"spend": -0.1, "pipeline": 0.9},
            ]);
            p["resolved"]["style"]["axis_x"]["scale"] =
                serde_json::json!({"continuous": {"zero": true}});
        }));
        let tags = x_rule_tags(&svg);
        assert_eq!(tags.len(), 1, "the joined top edge 0 holds the rule: {svg}");
        assert!(attr(tags[0], "x1") > 0.0, "at the right edge, not off-plot: {tags:?}");
    }

    /// A string-spelled authored domain is what the scale renders (`0.2..0.8`);
    /// the gate must read that same pair, so no rule paints at the off-plot
    /// position the data extent `0..0.6` would have implied.
    #[test]
    fn x_threshold_gate_reads_the_coerced_string_domain() {
        let svg = render_json(x_threshold_payload(|p| {
            p["rows"] = serde_json::json!([
                {"spend": 0.0, "pipeline": 0.1},
                {"spend": 0.3, "pipeline": 0.5},
                {"spend": 0.6, "pipeline": 0.9},
            ]);
            p["resolved"]["style"]["axis_x"]["scale"] =
                serde_json::json!({"continuous": {"domain": ["0.2", "0.8"]}});
        }));
        assert!(x_rule_tags(&svg).is_empty(), "0 lies outside the rendered 0.2..0.8: {svg}");
    }

    /// Regression-shaped (mirrors `zero_rule_suppressed_by_threshold_
    /// visible_false_even_with_grid_visible`): `axis_x.grid.threshold.
    /// visible` is an independent off-switch from `axis_x.grid.visible` —
    /// the corpus's own `p10_switch_off` control.
    #[test]
    fn x_threshold_suppressed_by_threshold_visible_false_even_with_grid_visible() {
        let svg = render_json(x_threshold_payload(|p| {
            p["resolved"]["style"]["axis_x"]["grid"]["threshold"] =
                serde_json::json!({"visible": false, "color": X_RULE_COLOR, "width": 2.0});
        }));
        assert!(x_rule_tags(&svg).is_empty(), "{svg}");
    }

    /// Mirrors `zero_rule_reads_is_quantitative_not_the_ports_own_y_
    /// nominal_guess`: the gate reads the RESOLVED `axis_x.is_quantitative`
    /// flag, never this file's own `x_nominal` channel-kind heuristic — an
    /// all-numeric x column (so the port's own heuristic takes the
    /// continuous branch regardless) with `is_quantitative: false` still
    /// refuses both rules.
    #[test]
    fn x_threshold_reads_is_quantitative_not_the_ports_own_heuristic() {
        let svg = render_json(x_threshold_payload(|p| {
            p["resolved"]["style"]["axis_x"]["is_quantitative"] = serde_json::json!(false);
        }));
        assert!(x_rule_tags(&svg).is_empty(), "{svg}");
    }

    /// `build_zero_rule_if_applicable`'s x-side mirror: an AUTHORED
    /// `scale.continuous.domain` excluding both 0 and 1 suppresses both
    /// rules outright, even with the percent format and threshold style
    /// otherwise satisfied. An authored domain also routes to the legacy
    /// arm (`x_scale_untouched` requires none), so this exercises the same
    /// gate as `legacy_arm_emits_x_threshold_rules_too`.
    #[test]
    fn x_threshold_suppressed_by_an_authored_domain_excluding_both() {
        let mut json = x_threshold_payload(|_| {});
        json["resolved"]["style"]["axis_x"]["scale"] =
            serde_json::json!({"continuous": {"domain": [10.0, 50.0], "zero": true}});
        json["rows"] = serde_json::json!([
            {"spend": 12.0, "pipeline": 0.15},
            {"spend": 30.0, "pipeline": 0.62},
            {"spend": 50.0, "pipeline": 1.0},
        ]);
        let svg = render_json(json);
        assert!(x_rule_tags(&svg).is_empty(), "{svg}");
    }

    /// Regression (`charts/extensibility_0::bubble_scatter`): a baseline rule
    /// nulls the `size` it would inherit (`full_rule_at`,
    /// `emitters/_cartesian.py:1410-1412`), so a size-channel scatter draws the
    /// y rule and the x rule exactly as one without a size channel does, both
    /// beneath the points.
    #[test]
    fn baseline_rules_draw_on_a_size_channel_scatter() {
        let mut json = make_scatter_payload_with_size(true);
        json["rows"] = serde_json::json!([
            {"spend": -18.0, "pipeline": -50.0, "segment": "SMB", "deals": 12.0},
            {"spend": 9.0, "pipeline": 60.0, "segment": "Mid", "deals": 78.0},
            {"spend": 27.0, "pipeline": 120.0, "segment": "SMB", "deals": 156.0},
        ]);
        json["resolved"]["style"]["axis_x"]["grid"]["visible"] = serde_json::json!(true);
        json["resolved"]["style"]["axis_x"]["grid"]["threshold"] =
            serde_json::json!({"color": X_RULE_COLOR, "width": 2.0});
        json["resolved"]["style"]["axis_y"]["zero_anchored"] = serde_json::json!(true);
        // The baked ladder spans the rows, so every mark lands in the plot.
        let ay = &mut json["resolved"]["style"]["axis_y"];
        ay["domain_min"] = serde_json::json!(-60.0);
        ay["domain_max"] = serde_json::json!(130.0);
        ay["tick_values"] = serde_json::json!([-50.0, 0.0, 50.0, 100.0]);
        set_zero_style(&mut json, true);
        let svg = render_json(json);
        let y_rule = zero_rule_tag(&svg).unwrap_or_else(|| panic!("y rule drawn: {svg}"));
        let x_rules = x_rule_tags(&svg);
        assert_eq!(x_rules.len(), 1, "one x rule: {svg}");
        let first_point = svg.find("<path transform=\"translate(").expect("points");
        for rule in [y_rule, x_rules[0]] {
            assert!(svg.find(rule).unwrap() < first_point, "beneath the points: {svg}");
        }
    }

    // ── A resolved format's affix on the tooltip and the axes ────────────

    /// Oracle: production's scatter tooltip (`axis_y.labels.format: usd`,
    /// `{spec: integer, prefix: "US$"}`, x quantitative) reads
    /// `v: US$140; s: 18`: the y row wears the slot, the x row keeps its digits
    /// alone (`_scatter_roles`, `structured_tooltip.py:567-580`).
    #[test]
    fn a_tooltip_row_paints_its_affix_on_y_and_not_on_a_quantitative_x() {
        let mut p = make_scatter_payload(true);
        p["resolved"]["style"]["tooltip_format"] = serde_json::json!({"spec": ",.2~f", "prefix": "US$"});
        let svg = render_json(p);
        assert!(svg.contains("aria-label=\"⁤SMB; Pipeline: US$140; Spend: 18\""), "{svg}");
    }

    /// A quantitative x that names a format of its own (`x_tooltip_format`)
    /// paints that slot, affix included.
    #[test]
    fn a_quantitative_x_paints_its_own_tooltip_format() {
        let mut p = make_scatter_payload(true);
        p["resolved"]["style"]["tooltip_format"] = serde_json::json!({"spec": ",.2~f", "prefix": "US$"});
        p["resolved"]["x_tooltip_format"] = serde_json::json!({"spec": ",.1f", "suffix": " k"});
        let svg = render_json(p);
        assert!(svg.contains("aria-label=\"⁤SMB; Pipeline: US$140; Spend: 18.0 k\""), "{svg}");
    }

    /// Oracle: production's wide scatter card (`NativeTooltipAffixFeature`,
    /// `native_tooltip.py:213`, `y: [v, w]` under `{spec: integer, prefix:
    /// "US$"}`) reads `s: 18; v, w: US$140; Series: v`.
    #[test]
    fn a_wide_scatter_value_row_paints_its_affix() {
        let mut p = make_wide_dot_band_payload();
        p["resolved"]["style"]["tooltip_format"] = serde_json::json!({"spec": ",.0f", "prefix": "US$"});
        let svg = render_json(p);
        assert!(
            svg.contains("aria-label=\"Team: Alpha; Unit price, Total cost: US$10; Series: Unit price\""),
            "{svg}"
        );
    }

    /// Both quantitative axes wear an authored suffix on every non-zero tick;
    /// beside an authored `labels.expr` the expression wins and the suffix is dropped.
    #[test]
    fn an_axis_affix_paints_and_yields_to_an_authored_labels_expr() {
        for axis in ["axis_x", "axis_y"] {
            let mut p = make_scatter_payload(false);
            p["resolved"]["style"][axis]["labels"]["format"] = serde_json::json!({"spec": ",.0f", "suffix": " CHF"});
            let payload: Payload = serde::Deserialize::deserialize(&p).unwrap();
            let svg = render(&payload, &test_fonts()).unwrap_or_else(|e| panic!("{axis}: {e}"));
            assert!(svg.contains(" CHF</text>"), "{axis}: {svg}");
            p["resolved"]["style"][axis]["labels"]["expr"] = serde_json::json!("datum.label");
            let payload: Payload = serde_json::from_value(p).unwrap();
            let svg = render(&payload, &test_fonts()).unwrap_or_else(|e| panic!("{axis}: {e}"));
            assert!(!svg.contains(" CHF"), "{axis}: {svg}");
        }
    }

    /// A bare `ticks.step` is the axis's `tickMinStep` (`apply_x_tick_cadence`):
    /// the count is capped at `floor(span / step) + 1`.
    #[test]
    fn a_bare_x_tick_step_caps_the_linear_tick_count() {
        let lin = |min_step| LinearX {
            raw: (0.0, 55.0),
            zero: false,
            nice: false,
            fit: false,
            pad: 0.0,
            bend: crate::vega_fit::Bend::Linear,
            tick_count: 6,
            min_step,
            format: None,
            ruler: None,
            tick_label: None,
            expr: None,
            case: None,
            rule_datums: [None, None],
        };
        assert_eq!(lin(None).at(400.0).unwrap().0.len(), 6);
        let (ticks, labels, _, _) = lin(Some(20.0)).at(400.0).unwrap();
        assert_eq!(ticks.len(), 3);
        assert_eq!(labels, ["0", "20", "40"]);
    }

    #[test]
    fn a_bare_x_tick_step_caps_the_log_tick_count() {
        let payload: Payload = serde_json::from_value(make_scatter_payload(false)).unwrap();
        let r: ScatterResolved = serde::Deserialize::deserialize(&payload.resolved).unwrap();
        let ax = &r.style.axis_x;
        let free = LogAxis::new(ax, 1.0, 100000.0, 10, None, 400.0).unwrap();
        let capped = LogAxis::new(ax, 1.0, 100000.0, 10, Some(50000.0), 400.0).unwrap();
        assert!(capped.ticks.len() < free.ticks.len(), "{:?} vs {:?}", capped.ticks, free.ticks);
    }

    /// The fill of the legend swatch that precedes `label`'s `.role-legend-label`
    /// group.
    fn swatch_fill(svg: &str, label: &str) -> String {
        let before = &svg[..svg.find(&format!(">{label}</text>")).expect("legend label drawn")];
        let swatch = &before[..before.rfind("<g class=\"mark-text role-legend-label\"").unwrap()];
        swatch[swatch.rfind("fill=\"").unwrap() + 6..].split('"').next().unwrap().to_string()
    }

    /// A colored dot plot with a visible legend and a structured tooltip: the
    /// shape whose tooltip carries a `ROLE_SERIES` row.
    fn tooltip_dot_plot_payload() -> serde_json::Value {
        let mut p = make_dot_plot_payload(serde_json::json!([
            {"median_response_hours": 3.0, "team": "Bravo", "segment": "SMB"},
            {"median_response_hours": 1.0, "team": "Alpha", "segment": "Mid"},
        ]));
        p["resolved"]["color"] = serde_json::json!("segment");
        p["resolved"]["legend"]["visible"] = serde_json::json!(true);
        p["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.2~f");
        p
    }

    /// `_stamp_series_keys` (`converters/chart.py:467-488`) stamps every
    /// `.role-legend-label` group with its untruncated series key when a
    /// tooltip names a `ROLE_SERIES` row and none ranks it with a `ROLE_ORDER`.
    #[test]
    fn a_dot_plot_legend_stamps_each_entry_with_its_series_key() {
        let svg = render_json(tooltip_dot_plot_payload());
        for series in ["Mid", "SMB"] {
            assert!(
                svg.contains(&format!(
                    "<g class=\"mark-text role-legend-label\" pointer-events=\"none\" \
                     data-dbt-series=\"{series}\">"
                )),
                "{series}: {svg}"
            );
        }
    }

    /// Vega ellipsizes a label past `labelLimit`, but production stamps the
    /// scenegraph's pre-truncation text (`converters/chart.py:457-461`): two
    /// long series sharing a drawn prefix must still carry distinct join keys.
    #[test]
    fn a_truncated_legend_label_is_stamped_with_its_full_series_key() {
        let long_a = "Quarterly enterprise segment with a very long account tier name A";
        let long_b = "Quarterly enterprise segment with a very long account tier name B";
        let mut p = tooltip_dot_plot_payload();
        p["rows"] = serde_json::json!([
            {"median_response_hours": 3.0, "team": "Bravo", "segment": long_a},
            {"median_response_hours": 1.0, "team": "Alpha", "segment": long_b},
        ]);
        let svg = render_json(p);
        for series in [long_a, long_b] {
            assert!(svg.contains(&format!("data-dbt-series=\"{series}\">")), "{series}: {svg}");
        }
        assert!(!svg.contains(&format!(">{long_a}</text>")), "label is drawn ellipsized: {svg}");
        assert!(svg.contains(&format!("\u{2026}<title>{long_a}</title></text>")), "the cut label reveals its full name on hover: {svg}");
    }

    /// `(swatch fill, data-dbt-series, drawn text)` of every stamped legend entry, in
    /// drawn order.
    fn stamped_entries(svg: &str) -> Vec<(String, String, String)> {
        let groups: Vec<&str> = svg.split("<g class=\"mark-text role-legend-label\"").collect();
        (1..groups.len())
            .map(|i| {
                let fill =
                    groups[i - 1].rsplit("fill=\"").next().unwrap().split('"').next().unwrap();
                let key = groups[i]
                    .split("data-dbt-series=\"")
                    .nth(1)
                    .unwrap()
                    .split('"')
                    .next()
                    .unwrap();
                let text = groups[i].split("</text>").next().unwrap().split("<title>").next().unwrap().rsplit('>').next().unwrap();
                (fill.to_string(), key.to_string(), text.to_string())
            })
            .collect()
    }

    /// An authored `legend.values` that reorders and drops entries keeps each
    /// drawn entry's swatch (the full domain's slot), stamp (its full name) and
    /// text together, including an ellipsized label.
    #[test]
    fn a_reordered_subset_legend_keeps_each_entrys_swatch_stamp_and_text_together() {
        let long = "Quarterly enterprise segment with a very long account tier name";
        let rows = serde_json::json!([
            {"median_response_hours": 3.0, "team": "Bravo", "segment": "SMB"},
            {"median_response_hours": 1.0, "team": "Alpha", "segment": "Mid"},
            {"median_response_hours": 2.0, "team": "Charlie", "segment": long},
        ]);
        let palette = serde_json::json!(["#3164a3", "#779bc9", "#608470"]);
        let mut full = tooltip_dot_plot_payload();
        full["rows"] = rows.clone();
        full["resolved"]["palette"] = palette.clone();
        let domain = stamped_entries(&render_json(full));
        let keys: Vec<&str> = domain.iter().map(|(_, key, _)| key.as_str()).collect();
        assert_eq!(keys, ["Mid", long, "SMB"], "{domain:?}");
        let fills: Vec<&str> = domain.iter().map(|(fill, _, _)| fill.as_str()).collect();
        assert_eq!(fills, ["#3164a3", "#779bc9", "#608470"], "{domain:?}");

        let mut authored = tooltip_dot_plot_payload();
        authored["rows"] = rows;
        authored["resolved"]["palette"] = palette;
        authored["resolved"]["legend"]["values"] = serde_json::json!(["SMB", long]);
        let drawn = stamped_entries(&render_json(authored));
        let (mid, long_entry, smb) = (&domain[0], &domain[1], &domain[2]);
        assert!(long_entry.2.ends_with('\u{2026}') && long_entry.2 != long, "{domain:?}");
        let expected = [smb, long_entry];
        assert_eq!(drawn, expected.map(Clone::clone), "dropped {mid:?}");
    }

    /// The same chart stamps nothing when its tooltip ranks the series (a
    /// board color scale adds a `ROLE_ORDER` row), when it has no tooltip, or
    /// when the tooltip names no `ROLE_SERIES` row (a plain scatter's header
    /// is the swatched color value).
    #[test]
    fn a_legend_stamps_no_series_key_unless_the_tooltip_promotes_a_unranked_series() {
        let mut ranked = tooltip_dot_plot_payload();
        ranked["resolved"]["category_colors"] = serde_json::json!(
            [{"field": "segment", "slots": {"SMB": 0, "Mid": 1}, "overrides": {}}]
        );
        let mut untipped = tooltip_dot_plot_payload();
        untipped["resolved"]["style"]["tooltip_format"] = serde_json::json!("");
        let mut plain = make_scatter_payload(true);
        plain["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.2~f");
        for (what, p) in [("ranked", ranked), ("no tooltip", untipped), ("plain scatter", plain)] {
            let svg = render_json(p);
            assert!(svg.contains("role-legend-label"), "{what}: {svg}");
            assert!(!svg.contains("data-dbt-series"), "{what}: {svg}");
        }
    }

    /// An authored `legend.values` (`apply_legend_entry_order`,
    /// `emitters/scatter.py:416-420`) picks and orders the entries drawn; each
    /// keeps the swatch its place in the sorted domain gave it.
    #[test]
    fn authored_legend_values_reorder_the_plain_color_legend_keeping_each_swatch() {
        let mut p = make_scatter_payload(true);
        p["resolved"]["legend"]["values"] = serde_json::json!(["SMB", "mid"]);
        let svg = render_json(p);
        assert_eq!(legend_labels(&svg, &["Mid", "SMB"]), ["SMB", "Mid"], "{svg}");
        assert_eq!(swatch_fill(&svg, "Mid"), "#3164a3", "{svg}");
        assert_eq!(swatch_fill(&svg, "SMB"), "#779bc9", "{svg}");

        let mut subset = make_scatter_payload(true);
        subset["resolved"]["legend"]["values"] = serde_json::json!(["Mid", "Nothing"]);
        let svg = render_json(subset);
        assert_eq!(legend_labels(&svg, &["Mid", "SMB"]), ["Mid"], "{svg}");
    }

    /// The wide fold's `legend.values` resolves against the humanized label or
    /// the raw measure name (`wide_legend_aliases`).
    #[test]
    fn authored_legend_values_reorder_the_wide_fold_legend_by_label_or_raw_name() {
        let mut p = make_wide_dot_band_payload();
        p["resolved"]["legend"]["values"] = serde_json::json!(["unit_price", "Total cost"]);
        let svg = render_json(p);
        let drawn = legend_labels(&svg, &["Total cost", "Unit price"]);
        assert_eq!(drawn, ["Unit price", "Total cost"], "{svg}");
        assert_eq!(swatch_fill(&svg, "Total cost"), "#3164a3", "{svg}");
        assert_eq!(swatch_fill(&svg, "Unit price"), "#779bc9", "{svg}");
    }

    #[test]
    fn an_empty_legend_values_list_draws_the_title_alone() {
        let mut p = make_scatter_payload(true);
        p["resolved"]["legend"]["values"] = serde_json::json!([]);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        assert!(!svg.contains("role-legend-label"), "no entry draws: {svg}");
    }

    /// The legacy margin arm draws its legend through the shared Vega legend
    /// too: label groups carry the `.role-legend-label` hook, and an authored
    /// `legend.values` picks and orders its entries.
    #[test]
    fn the_fit_arm_legend_is_the_shared_legend() {
        let mut p = fit_unbaked_payload(&[(10.0, 100.0), (20.0, 180.0), (30.0, 220.0)]);
        p["resolved"]["color"] = serde_json::json!("segment");
        p["resolved"]["legend"]["visible"] = serde_json::json!(true);
        for (row, series) in p["rows"].as_array_mut().unwrap().iter_mut().zip(["S", "A", "B"]) {
            row["segment"] = serde_json::json!(series);
        }
        p["resolved"]["legend"]["values"] = serde_json::json!(["S", "A"]);
        p["resolved"]["palette"] = serde_json::json!(["#3164a3", "#779bc9", "#608470"]);
        let svg = render_json(p);
        assert_eq!(svg.matches("role-legend-label").count(), 2, "{svg}");
        assert_eq!(legend_labels(&svg, &["A", "B", "S"]), ["S", "A"], "{svg}");
        assert_eq!(swatch_fill(&svg, "A"), "#3164a3", "{svg}");
        assert_eq!(swatch_fill(&svg, "S"), "#608470", "{svg}");
    }

    fn line_layer(y: &str, label: &str) -> serde_json::Value {
        serde_json::json!({
            "type": "line",
            "line_mark": {
                "disconnected_width": 0.9,
                "stroke": {"width": 4.0, "cap": "round", "join": "round"},
                "curve": "linear",
                "labels": {"visible": false}
            },
            "point_mark": {"size": 46.24, "filled": true, "stroke_width": 4.0, "labels": {"visible": false}},
            "axis_y": {"position": null},
            "x": null, "y": y, "label": label, "color": null, "ink": "#608470", "query_name": null
        })
    }

    fn layered_payload(layer: serde_json::Value) -> serde_json::Value {
        let mut p = make_scatter_payload(false);
        for (row, trend) in p["rows"].as_array_mut().unwrap().iter_mut().zip([100.0, 400.0, 300.0]) {
            row["trend"] = serde_json::json!(trend);
        }
        p["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
        p["resolved"]["style"]["axis_y"]["title"]["visible"] = serde_json::json!(true);
        p["resolved"]["style"]["axis_y"]["tick_values"] = serde_json::json!([]);
        p["resolved"]["style"]["axis_y"]["domain_min"] = serde_json::Value::Null;
        p["resolved"]["style"]["axis_y"]["domain_max"] = serde_json::Value::Null;
        p["resolved"]["layers"] = serde_json::json!([layer]);
        p
    }

    /// Marks sharing the value scale share its axis title: one title joining
    /// the base's and the layer's names, each once.
    #[test]
    fn a_layer_on_the_shared_scale_merges_the_y_titles() {
        let svg = render_json(layered_payload(line_layer("trend", "Trend")));
        assert!(svg.contains(">Pipeline, Trend</text>"), "{svg}");
    }

    /// A layer pinned to a side owns a scale and an axis: two titles, neither merged.
    #[test]
    fn a_pinned_layer_titles_its_own_axis() {
        let mut layer = line_layer("trend", "Trend");
        layer["axis_y"] = serde_json::json!({"position": "right"});
        let svg = render_json(layered_payload(layer));
        assert!(svg.contains(">Pipeline</text>") && svg.contains(">Trend</text>"), "{svg}");
        assert!(!svg.contains("Pipeline, Trend"), "{svg}");
    }

    /// The layer union returns Vega-Lite's default `nice`, which rounds a baked bound too.
    #[test]
    fn a_layered_measure_domain_rounds_a_baked_bound() {
        let mut ax: crate::common::AxisStyle = serde_json::from_value(axis_json(None)).unwrap();
        ax.domain_max = Some(254.5);
        assert_eq!(super::layout::unauthored_measure_domain(&ax, 140.0, 250.0, 100.0, false).1, 254.5);
        assert_eq!(super::layout::unauthored_measure_domain(&ax, 140.0, 250.0, 100.0, true).1, 260.0);
    }

    /// A layer outside any family-specific shape is named, not mis-parsed.
    #[test]
    fn an_unknown_layer_type_on_a_scatter_is_rejected_by_name() {
        let mut layer = line_layer("trend", "Trend");
        layer["type"] = serde_json::json!("heatmap");
        let payload: Payload = serde_json::from_value(layered_payload(layer)).unwrap();
        let err = render(&payload, &test_fonts()).unwrap_err();
        assert!(err.to_string().contains("\"heatmap\""), "{err}");
    }

    /// A scatter base's layer value labels are a text layer on the base's own x
    /// field: a layer authoring another x moves its marks, never its captions.
    #[test]
    fn a_layer_with_its_own_x_prints_its_values_at_the_base_x() {
        let label_x = |own_x: Option<&str>| {
            let mut layer = line_layer("trend", "Trend");
            layer["line_mark"]["labels"] = serde_json::json!({"visible": true});
            layer["x"] = own_x.map_or(serde_json::Value::Null, |x| serde_json::json!(x));
            let mut p = layered_payload(layer);
            for (row, other) in p["rows"].as_array_mut().unwrap().iter_mut().zip([27.0, 39.0, 18.0]) {
                row["other"] = serde_json::json!(other);
            }
            with_board_font(&mut p);
            let svg = render_json(p);
            let text = svg.find(">100</text>").unwrap_or_else(|| panic!("the first caption: {svg}"));
            let tag = &svg[svg[..text].rfind("<text").unwrap()..text];
            tag.split("x=\"").nth(1).and_then(|rest| rest.split('"').next()).expect("an x").to_string()
        };
        assert_eq!(label_x(None), label_x(Some("other")));
    }

    /// Production fails to compile a facet whose layer owns a value scale: a pinned
    /// layer, and a categorical y's layer (`child_layer_0_y`) alike.
    #[test]
    fn a_facet_whose_layer_owns_a_value_scale_is_refused() {
        for nominal_y in [false, true] {
            let mut p = layered_payload(line_layer("trend", "Trend"));
            p["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("right");
            p["resolved"]["style"]["axis_y"]["tick_values"] = serde_json::json!([150.0, 200.0, 250.0]);
            p["resolved"]["style"]["axis_y"]["domain_min"] = serde_json::json!(131.5);
            p["resolved"]["style"]["axis_y"]["domain_max"] = serde_json::json!(254.5);
            p["resolved"]["multiples"] = serde_json::json!({"rows": null, "columns": "panel", "scale": "shared"});
            p["resolved"]["panel_axes"] = serde_json::json!([{"field": "panel", "values": ["\"P0\"", "\"P1\""]}]);
            for (row, (x, y)) in p["rows"].as_array_mut().unwrap().iter_mut().zip([("a", "u"), ("b", "v"), ("c", "u")]) {
                row["panel"] = serde_json::json!("P0");
                row["spend"] = serde_json::json!(x);
                if nominal_y {
                    row["pipeline"] = serde_json::json!(y);
                }
            }
            if !nominal_y {
                p["resolved"]["layers"][0]["axis_y"] = serde_json::json!({"position": "left"});
            }
            let payload: Payload = serde_json::from_value(p).unwrap();
            let err = render(&payload, &test_fonts()).unwrap_err();
            assert!(err.to_string().contains("multiples with a dual-axis layer not supported"), "nominal_y {nominal_y}: {err}");
        }
    }

    /// A faceted scatter's panel reaches its annotations through the axis's raw categories, not
    /// the text it paints: a cased axis keeps the band the annotation names.
    #[test]
    fn a_faceted_category_x_annotation_finds_its_raw_band_under_a_font_case() {
        let contract: Vec<serde_json::Value> = serde_json::from_str(ANNOTATION_CONTRACT).unwrap();
        let mut annotation = contract.into_iter().find(|a| a["index"] == 5).unwrap();
        annotation["x"]["value"] = serde_json::json!("a");
        let render_with = |annotations: Vec<serde_json::Value>| {
            let mut p = make_scatter_payload(false);
            p["resolved"]["multiples"] = serde_json::json!({"rows": null, "columns": "panel", "scale": "shared"});
            p["resolved"]["panel_axes"] = serde_json::json!([{"field": "panel", "values": ["\"P0\"", "\"P1\""]}]);
            p["resolved"]["style"]["axis_x"]["labels"]["font"]["case"] = serde_json::json!("upper");
            p["resolved"]["annotations"] = serde_json::json!(annotations);
            for (row, (panel, x)) in p["rows"].as_array_mut().unwrap().iter_mut().zip([("P0", "a"), ("P0", "b"), ("P1", "c")]) {
                row["panel"] = serde_json::json!(panel);
                row["spend"] = serde_json::json!(x);
            }
            render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders")
        };
        let bare = render_with(Vec::new());
        assert!(bare.contains(">A</text>"), "{bare}");
        assert_ne!(render_with(vec![annotation]), bare, "the annotation paints its band");
    }

    const ANNOTATION_CONTRACT: &str = include_str!("../../../fixtures/annotations/contract.json");

    /// Contract entry `index`, re-anchored onto `make_scatter_payload`'s numbers.
    fn annotation(index: usize, x: serde_json::Value, y: serde_json::Value) -> serde_json::Value {
        let all: Vec<serde_json::Value> = serde_json::from_str(ANNOTATION_CONTRACT).unwrap();
        let mut a = all.into_iter().find(|a| a["index"] == index).unwrap();
        a["x"] = x;
        a["y"] = y;
        a
    }

    fn value(v: f64) -> serde_json::Value {
        serde_json::json!({"kind": "value", "value": v})
    }

    fn range(from: f64, to: f64) -> serde_json::Value {
        serde_json::json!({"kind": "range", "from": from, "to": to})
    }

    fn annotated_payload(annotations: Vec<serde_json::Value>) -> serde_json::Value {
        let mut json = make_scatter_payload(false);
        json["resolved"]["annotations"] = serde_json::Value::Array(annotations);
        json
    }

    fn annotated_scatter(annotations: Vec<serde_json::Value>) -> String {
        render_json(annotated_payload(annotations))
    }

    fn first_point_x(svg: &str) -> f64 {
        let t = svg.split("<path transform=\"translate(").nth(1).expect("a point");
        t.split(',').next().unwrap().parse().unwrap()
    }

    #[test]
    fn annotations_paint_behind_and_over_the_marks() {
        let svg = annotated_scatter(vec![
            annotation(6, range(20.0, 30.0), serde_json::Value::Null),
            annotation(0, serde_json::Value::Null, value(200.0)),
        ]);
        let background = svg.find("annotations-background").expect("a background group");
        let foreground = svg.find("annotations-foreground").expect("a foreground group");
        let mark = svg.find("<path transform=\"translate(").expect("a point mark");
        assert!(background < mark && mark < foreground, "{svg}");
        assert!(svg.contains("<clipPath id=\"c-ann0\">"), "{svg}");
    }

    #[test]
    fn a_scatter_stack_runs_tint_rule_points_text() {
        let mut rule = annotation(0, serde_json::Value::Null, value(200.0));
        rule["plane"] = "background".into();
        let svg = annotated_scatter(vec![annotation(6, range(20.0, 30.0), serde_json::Value::Null), rule]);
        let at = |needle: &str| svg.find(needle).unwrap_or_else(|| panic!("{needle} missing: {svg}"));
        let group = at("annotations-background");
        let tint = group + svg[group..].find("<rect").expect("a tint");
        let line = group + svg[group..].find("<line").expect("a rule");
        let point = at("<path transform=\"translate(");
        let text = at("annotations-text\"><g data");
        assert!(tint < line && line < point && point < text, "{svg}");
    }

    #[test]
    fn a_side_legend_clears_trend_text_outside_the_plot() {
        let mut json = make_wide_quantitative_x_payload();
        let mut trend = annotation(12, serde_json::Value::Null, serde_json::Value::Null);
        trend["text"] = "a trend label wider than the gap".into();
        trend["trend"] = serde_json::json!({"line": [[10.0, 10.0], [30.0, 20.0]], "interval": null});
        trend["style"]["text"]["position"] = serde_json::json!({"edge": "right", "align": "center", "overlay": false, "auto_edge": false, "auto_align": false});
        json["resolved"]["annotations"] = serde_json::json!([trend]);
        let svg = render_json(json);
        let text_x: f64 = svg.split("<text x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
        let label = svg.split("role-legend-label").nth(1).unwrap();
        let legend_x: f64 = label.split("<text x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
        // 32 characters at 11px are wider than 100px.
        assert!(legend_x > text_x + 100.0, "legend at {legend_x}, trend text at {text_x}: {svg}");
    }

    #[test]
    fn a_corner_note_moves_off_a_point_and_needs_no_halo() {
        let corner = |align: &str| {
            let mut note = annotation(10, serde_json::Value::Null, serde_json::Value::Null);
            note["panel"] = serde_json::Value::Null;
            note["style"]["text"]["halo_over_marks"] = true.into();
            note["style"]["text"]["position"] = serde_json::json!({"edge": "top", "align": align, "overlay": true, "auto_edge": false, "auto_align": false});
            annotated_scatter(vec![note])
        };
        let haloed = |svg: &str| svg.contains("stroke-linejoin=\"round\">Dashed = forecast");
        let attr = |svg: &str, from: &str, name: &str| -> f64 {
            let at = svg.find(from).unwrap_or_else(|| panic!("{from} missing: {svg}"));
            svg[at..].split(&format!("{name}=\"")).nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        // The highest point sits in the top-end corner: the text there slides
        // in from the plot's right edge, off the point.
        let end = corner("end");
        assert!(attr(&end, "annotations-text", "x") < attr(&end, "<clipPath", "width"), "{end}");
        assert!(!haloed(&end) && !haloed(&corner("start")));
    }

    #[test]
    fn no_annotations_paint_nothing() {
        let svg = annotated_scatter(Vec::new());
        assert!(!svg.contains("annotations"), "{svg}");
        assert!(!svg.contains("-ann0"), "{svg}");
    }

    #[test]
    fn an_annotation_x_past_the_data_widens_the_x_extent() {
        let plain = first_point_x(&annotated_scatter(Vec::new()));
        let band = annotation(6, range(500.0, 900.0), serde_json::Value::Null);
        let widened = first_point_x(&annotated_scatter(vec![band]));
        assert!(widened < plain - 1.0, "the first point moves left: {plain} -> {widened}");
    }

    #[test]
    fn rule_text_above_the_top_tick_reserves_top_margin() {
        let top = |svg: &str| -> f64 {
            let g = svg.split("<g transform=\"translate(").nth(1).unwrap();
            g.split(')').next().unwrap().split(',').nth(1).unwrap().parse().unwrap()
        };
        let plain = annotated_scatter(Vec::new());
        let ruled = annotated_scatter(vec![annotation(0, serde_json::Value::Null, value(254.0))]);
        assert!(top(&ruled) > top(&plain) + 5.0, "{} -> {}", top(&plain), top(&ruled));
    }

    /// The `translate` of every point mark, in row order.
    fn point_centers(svg: &str) -> Vec<(f64, f64)> {
        svg.split("<path transform=\"translate(")
            .skip(1)
            .map(|t| {
                let (x, y) = t.split(')').next().unwrap().split_once(',').unwrap();
                (x.trim().parse().unwrap(), y.trim().parse().unwrap())
            })
            .collect()
    }

    #[test]
    fn bent_axes_map_annotations_through_the_marks_bend() {
        for (kind, extra) in [("sqrt", serde_json::json!({})), ("pow", serde_json::json!({"pow": {"exponent": 2.0}})), ("symlog", serde_json::json!({"symlog": {"constant": 10.0}}))] {
            let mut json = annotated_payload(vec![
                annotation(0, serde_json::Value::Null, value(200.0)),
                annotation(0, value(27.0), serde_json::Value::Null),
            ]);
            let mut continuous = extra.clone();
            continuous["type"] = serde_json::json!(kind);
            for axis in ["axis_x", "axis_y"] {
                json["resolved"]["style"][axis]["scale"] = serde_json::json!({"continuous": continuous.clone()});
            }
            let svg = render_json(json);
            // The second row sits at (27, 200), where both rules cross.
            let (px, py) = point_centers(&svg)[1];
            let fg = svg.split("annotations-foreground").nth(1).unwrap();
            let attr = |line: &str, name: &str| -> f64 {
                line.split(&format!(" {name}=\"")).nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
            };
            let lines: Vec<&str> = fg.split("<line").skip(1).collect();
            let horizontal = lines.iter().find(|l| attr(l, "y1") == attr(l, "y2")).unwrap();
            let vertical = lines.iter().find(|l| attr(l, "x1") == attr(l, "x2")).unwrap();
            assert!((attr(horizontal, "y1") - py).abs() < 0.01, "{kind}: the y rule runs at {py}: {fg}");
            assert!((attr(vertical, "x1") - px).abs() < 0.01, "{kind}: the x rule runs at {px}: {fg}");
        }
    }

    fn category(c: &str) -> serde_json::Value {
        serde_json::json!({"kind": "category", "value": c})
    }

    /// The first `<rect>` and `<line>` of the annotation planes.
    fn annotation_rect_and_line(svg: &str) -> (&str, &str) {
        let planes = &svg[svg.find("annotations-background").unwrap()..];
        let rect = planes.split("<rect").nth(1).unwrap().split('>').next().unwrap();
        let line = planes.split("<line").nth(1).unwrap().split('>').next().unwrap();
        (rect, line)
    }

    #[test]
    fn a_dot_plot_bands_a_category_row_and_rules_its_value_axis() {
        let mut json = make_dot_plot_payload(dot_rows("Charlie"));
        json["resolved"]["annotations"] = serde_json::json!([
            annotation(6, serde_json::Value::Null, category("Bravo")),
            annotation(0, value(2.0), serde_json::Value::Null),
        ]);
        let svg = render_json(json);
        let centers = point_centers(&svg);
        let (rect, line) = annotation_rect_and_line(&svg);
        // Rows sort by hours descending: Bravo, Charlie, Alpha, a step apart.
        let (bravo, charlie) = (centers[0].1, centers[2].1);
        let step = charlie - bravo;
        assert!((attr(rect, "y") - (bravo - step / 2.0)).abs() < 0.01, "{rect} {centers:?}");
        assert!((attr(rect, "height") - step).abs() < 0.01, "{rect} {centers:?}");
        assert!((attr(line, "x1") - centers[2].0).abs() < 0.01, "{line} {centers:?}");
    }

    #[test]
    fn a_category_x_bands_its_slot() {
        let mut json = make_scatter_payload(false);
        json["rows"] = serde_json::json!([
            {"team": "Alpha", "pipeline": 140.0},
            {"team": "Bravo", "pipeline": 200.0},
            {"team": "Charlie", "pipeline": 250.0},
        ]);
        json["resolved"]["x"] = serde_json::json!("team");
        json["resolved"]["style"]["axis_x"]["is_quantitative"] = serde_json::json!(false);
        json["resolved"]["annotations"] = serde_json::json!([
            annotation(6, category("Bravo"), serde_json::Value::Null),
            annotation(0, serde_json::Value::Null, value(200.0)),
        ]);
        let svg = render_json(json);
        let centers = point_centers(&svg);
        let (rect, line) = annotation_rect_and_line(&svg);
        let step = centers[1].0 - centers[0].0;
        assert!((attr(rect, "x") - (centers[1].0 - step / 2.0)).abs() < 0.01, "{rect} {centers:?}");
        assert!((attr(rect, "width") - step).abs() < 0.01, "{rect} {centers:?}");
        assert!((attr(line, "y1") - centers[1].1).abs() < 0.01, "{line} {centers:?}");
    }

    #[test]
    fn a_value_on_a_category_axis_is_a_payload_error() {
        let mut json = make_dot_plot_payload(dot_rows("Charlie"));
        json["resolved"]["annotations"] = serde_json::json!([annotation(0, serde_json::Value::Null, value(1.0))]);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let err = render(&payload, &test_fonts()).unwrap_err();
        assert!(err.to_string().contains("ERR-ANNOTATION-PAYLOAD"), "{err}");
    }

    /// Three points whose x are the dates given, in the order given, with the annotations beside them.
    fn dated_scatter(xs: [&str; 3], annotations: Vec<serde_json::Value>) -> serde_json::Value {
        let mut json = make_scatter_payload(false);
        json["resolved"]["style"]["label_usable_ratio"] = serde_json::json!(0.85);
        json["resolved"]["annotations"] = serde_json::Value::Array(annotations);
        for (row, x) in json["rows"].as_array_mut().unwrap().iter_mut().zip(xs) {
            row["spend"] = serde_json::json!(x);
        }
        json
    }

    fn date_value(day: &str) -> serde_json::Value {
        serde_json::json!({"kind": "value", "value": day})
    }

    /// Rows in query order or in time order draw the same marks and place an annotation on the same date.
    #[test]
    fn unordered_rows_draw_the_marks_and_annotations_of_ordered_rows() {
        let _locale = crate::time_locale::en_us_scope();
        let rule = || vec![annotation(0, date_value("2024-02-20"), serde_json::Value::Null)];
        let ordered = render_json(dated_scatter(["2024-01-15", "2024-02-15", "2024-03-15"], rule()));
        // The y values follow their rows, so reorder the whole row.
        let mut shuffled = dated_scatter(["2024-01-15", "2024-02-15", "2024-03-15"], rule());
        shuffled["rows"].as_array_mut().unwrap().reverse();
        let shuffled = render_json(shuffled);
        let marks = |svg: &str| -> Vec<String> {
            let mut marks: Vec<String> = crate::time_axis::testing::geometry(svg);
            marks.sort();
            marks
        };
        assert_eq!(marks(&ordered), marks(&shuffled));
    }

    /// Two points on one day, hours apart, sit hours apart on the axis.
    #[test]
    fn a_time_of_day_places_the_point() {
        let _locale = crate::time_locale::en_us_scope();
        let svg = render_json(dated_scatter(["2024-03-01T08:00:00", "2024-03-01T20:00:00", "2024-03-02T08:00:00"], Vec::new()));
        let xs: Vec<f64> = svg
            .split("<path transform=\"translate(")
            .skip(1)
            .map(|t| t.split(',').next().unwrap().parse().unwrap())
            .collect();
        assert_eq!(xs.len(), 3, "{svg}");
        assert!(((xs[1] - xs[0]) * 2.0 - (xs[2] - xs[0])).abs() < 0.01, "twelve hours is half of twenty-four: {xs:?}");
    }

    /// An offset timestamp sits where its UTC spelling does.
    #[test]
    fn an_offset_timestamp_point_sits_where_its_utc_spelling_does() {
        let _locale = crate::time_locale::en_us_scope();
        let offset = render_json(dated_scatter(["2024-03-01T23:30:00-05:00", "2024-03-03T01:00:00+05:30", "2024-03-04T08:00:00+0100"], Vec::new()));
        let utc = render_json(dated_scatter(["2024-03-02T04:30:00Z", "2024-03-02T19:30:00Z", "2024-03-04T07:00:00Z"], Vec::new()));
        assert_eq!(crate::time_axis::testing::geometry(&offset), crate::time_axis::testing::geometry(&utc));
    }

    /// On a New York board an aware point's tooltip header reads the New York clock; a naive one as written.
    #[test]
    fn an_aware_point_reads_its_tooltip_clock_in_the_board_zone() {
        let _locale = crate::time_locale::en_us_scope();
        let _zone = crate::time_zone::zone_scope("America/New_York");
        let headers = |xs: [&str; 3]| {
            let mut json = make_wide_quantitative_x_payload();
            json["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.0f");
            json["resolved"]["style"]["label_usable_ratio"] = serde_json::json!(0.85);
            for (row, x) in json["rows"].as_array_mut().expect("rows").iter_mut().zip(xs) {
                row["spend"] = serde_json::json!(x);
            }
            let svg = render_json(json);
            svg.split("aria-label=\"").skip(1).filter_map(|a| a.split('"').next()).filter(|a| a.contains("; ")).map(str::to_string).collect::<Vec<_>>()
        };
        let aware = headers(["2024-03-10T05:00:00Z", "2024-03-10T07:00:00Z", "2024-03-10T09:00:00Z"]);
        assert!(aware.iter().any(|a| a.contains("3:00am")) && !aware.iter().any(|a| a.contains("7:00am")), "{aware:?}");
        let naive = headers(["2024-03-10T05:00:00", "2024-03-10T07:00:00", "2024-03-10T09:00:00"]);
        assert!(naive.iter().any(|a| a.contains("7:00am")), "{naive:?}");
        // A sub-day tick reaches labels.expr as its instant, not floored to the day.
        let mut json = make_wide_quantitative_x_payload();
        for (row, x) in json["rows"].as_array_mut().expect("rows").iter_mut().zip(["2024-03-10T05:00:00Z", "2024-03-10T07:00:00Z", "2024-03-10T09:00:00Z"]) {
            row["spend"] = serde_json::json!(x);
        }
        json["resolved"]["style"]["label_usable_ratio"] = serde_json::json!(0.85);
        json["resolved"]["style"]["axis_x"]["labels"]["expr"] = serde_json::json!("timeFormat(datum.value, '%H') + 'h'");
        let svg = render_json(json);
        let hours: std::collections::BTreeSet<&str> = svg.split('>').filter_map(|t| t.strip_suffix("h</text")).collect();
        assert!(hours.contains("03") && hours.len() > 2, "{hours:?}");
    }

    /// A date-shaped x value that is no date (a February 30th) refuses by the code every x-axis family raises,
    /// as a null x does not: it is no value.
    #[test]
    fn a_date_x_value_that_is_no_date_is_refused_by_name() {
        let mut json = dated_scatter(["2024-01-15", "2024-02-30", "2024-03-15"], Vec::new());
        // Under a tenth of the column: the type law lets it through, the axis refuses the value.
        for month in 4..=12 {
            json["rows"].as_array_mut().unwrap().push(serde_json::json!({"spend": format!("2024-{month:02}-15"), "pipeline": 100.0 + f64::from(month)}));
        }
        let payload: Payload = serde_json::from_value(json).unwrap();
        let err = render(&payload, &test_fonts()).expect_err("an unparseable date x refuses");
        let text = err.to_string();
        assert!(text.contains("ERR-X-VALUE-NOT-A-DATE"), "{text}");
        for param in ["x_field=\"spend\"", "value=\"2024-02-30\""] {
            assert!(text.contains(param), "{param}: {text}");
        }
    }

    /// An authored x domain may end on a timestamp, which the UTC scale takes as the instant it is.
    #[test]
    fn an_authored_timestamp_domain_sets_the_utc_scale() {
        let _locale = crate::time_locale::en_us_scope();
        let mut json = dated_scatter(["2024-01-15", "2024-02-15", "2024-03-15"], Vec::new());
        json["resolved"]["style"]["axis_x"]["scale"]["continuous"]["domain"] = serde_json::json!(["2024-01-01T12:00:00", "2024-04-01T12:00:00"]);
        render_json(json);
    }

    // ── The family contract ──────────────────────────────────────────────

    fn contract_payload(mutate: impl FnOnce(&mut serde_json::Value)) -> Payload {
        let mut json = make_scatter_payload(true);
        mutate(&mut json);
        serde_json::from_value(json).unwrap()
    }

    /// `bind` reads no font: the channels' kinds and the rows layout reads are there before any size is known.
    #[test]
    fn bind_classifies_the_channels_and_prepares_the_rows() {
        let payload = contract_payload(|_| {});
        let quantitative = super::bind::bind(&payload).expect("scatter binds");
        let shape = &quantitative.shape;
        assert_eq!((shape.x_dates, shape.x_nominal, shape.y_nominal, shape.y_measure), (false, false, false, true));
        assert_eq!(quantitative.payload.rows.len(), 3);

        let payload = contract_payload(|json| {
            for (row, team) in json["rows"].as_array_mut().unwrap().iter_mut().zip(["a", "b", "c"]) {
                row["spend"] = serde_json::json!(team);
            }
        });
        let nominal_x = super::bind::bind(&payload).expect("scatter binds");
        assert!(nominal_x.shape.x_nominal && !nominal_x.shape.x_dates);

        let payload = contract_payload(|json| {
            for (row, day) in json["rows"].as_array_mut().unwrap().iter_mut().zip(["2024-01-01", "2024-02-01", "2024-03-01"]) {
                row["spend"] = serde_json::json!(day);
            }
        });
        let dated = super::bind::bind(&payload).expect("scatter binds");
        assert!(dated.shape.x_dates && !dated.shape.x_nominal);
    }

    /// An unfilled mark with no interior is refused at bind, ahead of any layout.
    #[test]
    fn bind_refuses_an_unfilled_mark_with_no_interior() {
        let payload = contract_payload(|json| json["resolved"]["style"]["point_mark"]["filled"] = serde_json::json!(false));
        let err = super::bind::bind(&payload).err().expect("bind refuses");
        assert!(err.to_string().contains("unfilled point marks need style.marks.point.fill"), "{err}");
    }

    #[test]
    fn a_scatter_hugs_nothing_and_takes_the_slot_height() {
        let payload = contract_payload(|_| {});
        let bound = super::bind::bind(&payload).expect("scatter binds");
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
        let payload = contract_payload(|json| json["resolved"]["title"] = serde_json::json!(long.repeat(4)));
        let bound = super::bind::bind(&payload).expect("scatter binds");
        let fonts = test_fonts();
        let measure = measure::Measure { fonts: &fonts };
        let cell = crate::layout::Rect { x: 0.0, y: 0.0, width: 420.0, height: 260.0 };
        let (laid, sunk) = crate::diagnostic::collect(|| super::layout::layout(&bound, cell, &measure));
        let laid = laid.expect("scatter lays out");
        assert!(sunk.is_empty(), "{sunk:?}");
        let codes: Vec<_> = laid.diagnostics.iter().map(|d| d.code).collect();
        assert_eq!(codes, ["WARN-CHART-TITLE-TRUNCATED"]);
        let [scene::Node::Svg { extent, .. }] = laid.nodes.as_slice() else { panic!("one root") };
        assert_eq!((extent.width, extent.height), (420.0, 260.0));
        let data = laid.data_box;
        assert!(data.x >= 0.0 && data.y >= 0.0 && data.x + data.width <= 420.0 && data.y + data.height <= 260.0);
        assert_eq!(laid.insets, crate::layout::Rect { x: 0.0, y: 0.0, width: 420.0, height: 260.0 }.insets_of(&data));
    }

    /// A `multiples` chart binds, but only `render` composes it: `layout` draws one plot and refuses.
    #[test]
    fn layout_refuses_a_multiples_chart() {
        let payload = contract_payload(|json| {
            json["resolved"]["multiples"] = serde_json::json!({"rows": null, "columns": "panel", "scale": "shared"});
            json["resolved"]["panel_axes"] = serde_json::json!([{"field": "panel", "values": ["\"P0\""]}]);
        });
        let bound = super::bind::bind(&payload).expect("a multiples chart binds");
        let fonts = test_fonts();
        let cell = crate::layout::Rect { x: 0.0, y: 0.0, width: payload.width, height: payload.height };
        let err = super::layout::layout(&bound, cell, &measure::Measure { fonts: &fonts }).err().expect("layout refuses");
        assert_eq!(err.to_string(), "scatter: layout draws one plot; a multiples chart is composed by render");
    }

    /// `render` is `bind`, `layout` into the payload's card, and `print`; the warnings reach the
    /// sink once, from the layout's own list.
    #[test]
    fn render_prints_the_layout_of_the_bound_chart() {
        let payload = contract_payload(|json| json["resolved"]["title"] = serde_json::json!("T ".repeat(300)));
        let bound = super::bind::bind(&payload).expect("scatter binds");
        let fonts = test_fonts();
        let cell = crate::layout::Rect { x: 0.0, y: 0.0, width: payload.width, height: payload.height };
        let laid = super::layout::layout(&bound, cell, &measure::Measure { fonts: &fonts }).unwrap();
        let (svg, sunk) = crate::diagnostic::collect(|| render(&payload, &fonts));
        assert_eq!(svg.unwrap(), print::print(&laid.nodes));
        assert!(!laid.diagnostics.is_empty(), "the payload must warn for forwarding to be tested");
        assert_eq!(sunk, laid.diagnostics);
    }
}
