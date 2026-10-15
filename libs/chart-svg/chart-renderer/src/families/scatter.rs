//! Scatter family — a quantitative x against either a quantitative y or a
//! categorical one (production's dot plot: a `point`-scale band per
//! category), OR a categorical x against a quantitative y (a `point`-scale
//! band per category on x, `scale.padding` read from the resolved axis —
//! the theme bakes `axis_x.scale.padding: 0`, unlike the dot plot's
//! hardcoded 0.5 on y, since only x's theme block carries that override),
//! with an optional nominal color channel, an optional quantitative size
//! channel (Vega's zero-anchored linear size scale on the marks, and a second
//! symbol legend stacked under the color legend) and an optional nominal shape
//! channel (the legacy arm draws color and shape, not size). x and y are never
//! BOTH categorical — the fit shape always keeps one continuous axis. A
//! wide (`y: [a, b]`) chart unfolds into long rows once, at the top of
//! `render`, before any other law runs — same seam as bar.rs/line.rs
//! (`common::unfold_wide_rows`'s own doc comment), landing a nominal x on
//! the fit arm with one colored point per measure per category (the
//! "dot/band plot").
//!
//! Unsupported → clear Err:
//! - multiples outside the date-x, measure-y, right-axis shape `render_facet`
//!   models (each other shape refuses by name)
//! - non-empty layers
//! - a shape channel with wide measures
//! - a log axis with an authored domain, non-positive data, or outside the fit
//!   arm; a size channel outside the fit arm, on a non-right legend, or with a
//!   non-numeric value
//! - a mixed numeric/categorical y column
//! - a nominal x channel together with a nominal y channel (no continuous
//!   axis left to fit against)
//! - a categorical x or y outside the fit shape — the legacy arm draws no
//!   categorical marks
//! - a non-numeric `sort.by`, or a time `labels.format` on a categorical y
//! - `wide_measures` (`y: [a, b]`) together with an authored `color:`
//!   dimension, or with a `size` channel — neither combination reaches the
//!   corpus, unlike bar's/line's own wide-measure ports

use crate::diagnostic::Diagnostic;
use std::collections::HashMap;
use std::collections::HashSet;
use std::fmt::Write as _;

use serde::Deserialize;
use serde_json::Value;

use crate::common::color_domain_of;
use crate::common::{
    format_y_label, row_num, row_str, symbol_radius, AxisStyle, ChartSort, Font,
    FormatSlot, SortDir, StyleChannel, TitleStyle,
};
use crate::families::cartesian::LinearScale;
use crate::families::facet_compose::{self, GhostStyle, PanelGeometry, PanelXAxis};
use crate::families::temporal::{infer_vega_type, VegaType};
use crate::families::color_scale::{tick_precision, ContinuousColor, GradientAt};
use crate::families::legend;
use crate::families::facet_title::{layout_title, TitleLayout, TitleSource};
use crate::families::multiples;
use crate::families::scatter_axis::TemporalXAxis;
use crate::families::legend::{size_legend_grid, stack_legends, VEGA_LEGEND_OFFSET, VEGA_LEGEND_TITLE_PADDING};
use crate::frame::{ChartFrame, FrameTitle};
use crate::svg::{py_float_str, xml_escape, xml_escape_attr, vega_escape_attr};
use crate::text::FontBook;
use crate::vega_fit;
use crate::Payload;

/// One rendered point: (x fraction, y fraction, color series, radius, own
/// row -- the row backs its structured-tooltip aria-label). Both fractions
/// are already resolved to [0, 1] of the plot box — continuous or nominal,
/// x and y each collapse to the same one representation before a point is
/// built, so nothing downstream re-derives a scale.
type PointDatum<'a> = (f64, f64, String, f64, &'a serde_json::Map<String, Value>);

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

pub fn render(payload: &Payload, fonts: &FontBook) -> Result<String, Diagnostic> {
    let mut r: ScatterResolved = serde_json::from_value(payload.resolved.clone())
        .map_err(|e| format!("scatter payload: {e}"))?;
    r.style.axis_x.refuse_unpainted_affix("scatter", "axis_x", false)?;
    r.style.axis_x.refuse_x_tick_cadence("scatter")?;
    r.style.axis_x.refuse_top_x_axis("scatter")?;
    r.style.axis_y.refuse_unpainted_affix("scatter", "axis_y", false)?;

    if !r.layers.is_empty() {
        return Err("scatter: layers not supported".into());
    }
    if r.shape.is_some() {
        if !r.wide_measures.is_empty() {
            return Err("scatter: a shape channel with wide_measures is not supported".into());
        }
    }
    if r.style.point_mark.shape.as_ref().is_some_and(|v| !v.is_string()) {
        return Err("scatter: an authored point_mark shape must be a symbol name".into());
    }
    if r.style.point_mark.filled == Some(false) {
        if r.style.point_mark.fill.is_none() {
            return Err("scatter: unfilled point marks need style.marks.point.fill".into());
        }
    }
    // A wide (`y: [a, b]`) chart unfolds into long rows once, here, before
    // any other law runs — same seam as bar.rs/line.rs (`common::
    // unfold_wide_rows`'s own doc comment). Scatter's fold has no
    // dimension/stack-order complexity to carry: `_wide_scatter_fold`
    // (`emitters/scatter.py:65-95`) passes `fold_order=raw_wide_series_
    // names(measures, dimension, data)`, and `raw_wide_series_names`
    // (`compile/resolve/chart/_wide_fields.py:142-161`) is `sorted(
    // measures)` with no dimension — production paints in SORTED measure
    // order, never the authored `wide_measures` array order — so this seam
    // sorts before folding; a `color:` dimension composes `value - measure`
    // labels like bar/line. Unlike bar/line, the row's own `WIDE_LABEL_FIELD` is overwritten
    // to the HUMANIZED text directly (not the raw measure name) —
    // production's real VL `calculate` expression (`_label_expression`)
    // bakes the humanized label straight onto the row, and scatter's
    // `color_domain`/`series_of` both read that field verbatim with no
    // further humanization step of their own, so this is the one
    // substitution that keeps them correct with no new plumbing.
    let payload_wide;
    let mut wide_series: Vec<String> = Vec::new();
    let payload: &Payload = if !r.wide_measures.is_empty() {
        let wide_labels = display_text::wide_measure_labels_for(&r.wide_measures, &r.field_labels);
        let mut fold_order = r.wide_measures.clone();
        fold_order.sort();
        let dimension = r.color.clone();
        let raw_names = crate::common::wide_series_names(&r.wide_measures, dimension.as_deref(), &payload.rows);
        // Display text per raw series name: the measure part humanized, a
        // `color:` dimension value kept verbatim ahead of it.
        let mut shown: HashMap<String, String> = HashMap::new();
        for measure in &r.wide_measures {
            match dimension.as_deref() {
                None => {
                    shown.insert(measure.clone(), wide_labels[measure].clone());
                }
                Some(dim) => {
                    for v in crate::common::wide_dimension_values(&payload.rows, dim) {
                        shown.insert(
                            format!("{v}{}{measure}", crate::common::WIDE_SERIES_SEPARATOR),
                            format!("{v}{}{}", crate::common::WIDE_SERIES_SEPARATOR, wide_labels[measure]),
                        );
                    }
                }
            }
        }
        wide_series = raw_names.iter().map(|raw| shown[raw].clone()).collect();
        let mut cloned = payload.clone();
        cloned.rows = crate::common::unfold_wide_rows(&payload.rows, &fold_order, dimension.as_deref())
            .into_iter()
            .map(|mut row| {
                if let Some(Value::String(raw)) = row.remove(crate::common::WIDE_LABEL_FIELD) {
                    row.insert(crate::common::WIDE_LABEL_FIELD.to_string(), Value::String(shown[&raw].clone()));
                }
                row
            })
            .collect();
        r.color = Some(crate::common::WIDE_LABEL_FIELD.to_string());
        payload_wide = cloned;
        &payload_wide
    } else {
        payload
    };

    // The fit path models production's compiled shape: a single unit spec
    // under `autosize: fit` with a right y axis — the measure ladder, or a
    // point scale when y is categorical (production's dot plot).
    // Everything else — a left y axis, a non-right legend, authored point
    // value labels, an x axis without an authored label format, an
    // authored x scale transform (a non-linear scale type, an authored
    // domain, nice:false, padding) or an unbaked measure ladder — keeps the
    // legacy heuristic margins.
    //
    // A column types the way production's `infer_vega_type_from_data` reads
    // it: its first ten cells decide, so a quantitative column drops a later
    // string row (`kept_reading`) and anything else is a nominal band, whose
    // domain order comes from `common::vega_ascending_sort`.
    let column_is_measure = |field: &str| infer_vega_type(&payload.rows, field) == VegaType::Quantitative;
    let x_numeric = column_is_measure(&r.x);
    let y_numeric = column_is_measure(&r.y);
    let x_nominal = !x_numeric;
    // A column of ISO dates types `temporal` (a UTC time scale), not a
    // category band (`type_inference.py`'s strict `all_temporal` gate). A
    // date-LIKE column (`2024-01`) that `detect_time_unit` buckets is also
    // `temporal` on a scatter (`resolve_cartesian_x_type`), which this arm
    // does not draw; any other date-like column stays a band.
    let (x_dates, x_nominal) = if x_nominal {
        let mut seen = HashSet::new();
        let distinct: Vec<String> = payload
            .rows
            .iter()
            .map(|row| row_str(row, &r.x))
            .filter(|v| !v.is_empty() && seen.insert(v.clone()))
            .collect();
        let refs: Vec<&str> = distinct.iter().map(String::as_str).collect();
        if crate::families::temporal::x_values_are_standard_dates(&refs)
            || crate::families::temporal::x_values_are_temporal_candidates(&refs)
                && crate::families::temporal::detect_time_unit(&refs).is_some()
        {
            (true, false)
        } else {
            (false, true)
        }
    } else {
        (false, false)
    };
    let labels_on = r.style.point_mark.labels.as_ref().is_some_and(|l| l.visible);
    // The fold tooltip below formats a nominal x only.
    if !wide_series.is_empty() && !x_nominal {
        return Err("scatter: wide_measures with a quantitative or date x is not supported".into());
    }
    if labels_on && !wide_series.is_empty() {
        return Err("scatter: point value labels with wide_measures are not supported".into());
    }
    let any_legend_shown = (r.color.is_some() || r.size.is_some()) && r.legend.visible;
    let ax_x = &r.style.axis_x;
    let x_cont = ax_x.scale.get("continuous");
    let x_scale_plain = x_cont.and_then(|c| c.get("domain")).is_none_or(Value::is_null)
        && ax_x.scale.get("nice").and_then(Value::as_bool) != Some(false)
        && ax_x.scale.get("padding").and_then(Value::as_f64).unwrap_or(0.0) == 0.0;
    let x_scale_untouched = x_cont
        .and_then(|c| c.get("type"))
        .and_then(Value::as_str)
        .is_none_or(|t| t == "linear")
        && x_scale_plain;
    let x_log = ax_x.is_quantitative && ax_x.is_log_scale();
    let y_nominal = !y_numeric;
    for (axis, field, nominal) in [("x", &r.x, x_nominal), ("y", &r.y, y_nominal)] {
        let kept_null_category = payload.rows.iter().any(|row| {
            row.get(field).is_none_or(Value::is_null)
                && kept_reading(row, &r, x_nominal, x_dates, y_nominal).is_some()
        });
        if nominal && kept_null_category {
            return Err(format!(
                "scatter: a null category {axis} is not supported — Vega-Lite's invalid filter \
                 reads quantitative and temporal fields only, so the row stays under a `null` band"
            ).into());
        }
    }
    if y_nominal && r.style.axis_y.labels.spec().is_some_and(crate::families::temporal::is_time_format) {
        // Production routes a time format on a categorical axis to a UTC
        // labelExpr that relabels every band (`emitters/scatter.py`); every
        // other format it simply drops, which is what rendering the raw
        // category strings already does.
        return Err("scatter: a time `labels.format` on a categorical y is not \
                    supported — production relabels every band through a UTC \
                    labelExpr"
            .into());
    }
    let y_log = r.style.axis_y.is_log_scale();
    // A log axis is never baked (`_resolve_cartesian_ticks` returns no
    // ladder for it): its ticks come from the scale itself.
    let y_measure =
        y_numeric && (!r.style.axis_y.tick_values.is_empty() || y_log);
    if (y_log || x_log) && r.multiples.is_some() {
        return Err("scatter: multiples with a log axis not supported".into());
    }
    if r.multiples.is_some() {
        return render_facet(
            payload,
            fonts,
            &r,
            FacetShape {
                x_dates,
                x_scale_untouched,
                x_nominal,
                y_nominal,
                y_measure,
                labels_on,
            },
        );
    }
    let use_vega_fit = (x_numeric || x_nominal || x_dates)
        && (y_nominal || y_measure)
        && r.style.axis_y.position.as_deref() == Some("right")
        && (x_nominal
            || x_dates && x_scale_untouched
            || (ax_x.labels.format.is_some() && (x_scale_untouched || x_log && x_scale_plain)));

    // The legacy arm's `has_legend` reads the right edge alone and draws one
    // measured column: any other placement, a floating one, a slid one, and a
    // shape legend (which Vega always stacks at the right) refuse there.
    if !use_vega_fit {
        let pos = &r.legend.position;
        if any_legend_shown && (pos.edge != "right" || pos.overlay || pos.align != legend::LegendAlign::Start) {
            return Err(format!(
                "scatter: legend.position={:?} (align {:?}, overlay {}) beside the legacy margin arm is not modeled",
                pos.edge, pos.align, pos.overlay
            ).into());
        }
    }
    if any_legend_shown && !use_vega_fit {
        legend::refuse_legacy_columns("scatter", &r.legend)?;
    }
    if labels_on {
        let ml = r.style.point_mark.labels.as_ref().expect("labels_on implies labels");
        if ml.field.is_some() {
            return Err("scatter: point value labels over another field are not supported".into());
        }
        if ml.position.as_deref().is_some_and(|p| p != "top") {
            return Err("scatter: point value labels at a position other than top are not supported".into());
        }
        if y_nominal {
            return Err("scatter: point value labels on a categorical y are not supported".into());
        }
        if r.color.is_some() || r.size.is_some() || r.shape.is_some() {
            return Err("scatter: point value labels beside a color, size or shape channel are not supported".into());
        }
    }
    // The legacy arm would leak the synthetic `WIDE_VALUE_FIELD`/
    // `WIDE_LABEL_FIELD` names as titles.
    if r.style.point_mark.filled == Some(false) && !use_vega_fit {
        return Err("scatter: unfilled point marks outside production's fit shape (a right y axis, an \
                    untouched x scale) are not supported"
            .into());
    }
    if r.style.point_mark.shape.is_some() && !use_vega_fit {
        return Err("scatter: an authored point shape outside production's fit shape (a right y axis, an \
                    untouched x scale) is not supported"
            .into());
    }
    if !wide_series.is_empty() && !use_vega_fit {
        return Err("scatter: wide_measures outside production's fit shape (a right y axis, an \
                    untouched x scale) are not supported"
            .into());
    }
    if labels_on && !use_vega_fit {
        return Err("scatter: point value labels outside production's fit shape (a right y axis, \
                    an untouched x scale) are not supported"
            .into());
    }
    if (y_log || x_log) && !use_vega_fit {
        return Err("scatter: a log axis outside production's fit shape (a right y axis, a \
                    right legend, an authored x label format, an untouched x scale) is not \
                    supported"
            .into());
    }
    if use_vega_fit {
        Ok(render_fit(payload, fonts, &r, y_nominal, x_nominal, x_dates, &wide_series, None).map(|(svg, _)| svg)?)
    } else if x_dates {
        // The legacy arm's point loop reads a numeric x, so a date x would
        // draw no marks at all.
        Err("scatter: a date x outside production's fit shape (a right y axis, a right \
             legend, an untouched x scale) is not supported"
            .into())
    } else if y_nominal || x_nominal {
        // The legacy arm's point loop reads a numeric x and y, so it would
        // draw no marks and no category labels at all.
        Err("scatter: a dot/band plot outside production's fit shape (a right y axis, a \
             right legend, an authored x label format, an untouched x scale) is not \
             supported"
            .into())
    } else {
        Ok(render_legacy(payload, fonts, &r)?)
    }
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
    if hi <= lo {
        return Err("scatter: size domain is degenerate (max ≤ min)".into());
    }
    Ok(Some((lo, hi)))
}

/// Vega-Lite's linear size scale over its fixed default point-mark range:
/// the drawn AREA for `v`.
fn size_area((lo, hi): (f64, f64), v: f64) -> f64 {
    let (r_lo, r_hi) = SIZE_SCALE_RANGE;
    r_lo + (v - lo) / (hi - lo) * (r_hi - r_lo)
}

/// A size channel's symbol legend (`legend.rs::size_legend_grid`), laid out
/// once and painted wherever the chart's legend stack puts it. It rides the
/// same `chart.legend` config as the color legend.
struct SizeLegend {
    title: String,
    labels: Vec<String>,
    cells: Vec<legend::SizeCell>,
    title_band: f64,
    glyphs: Vec<String>,
    w: f64,
    h: f64,
}

impl SizeLegend {
    fn new(
        r: &ScatterResolved,
        fonts: &FontBook,
        domain: (f64, f64),
        symbol: &str,
    ) -> Result<Self, String> {
        let size_field = r.size.as_deref().expect("a size legend needs a size channel");
        let title = legend::legend_title_text(size_field, &r.legend, &r.field_labels)?;
        let label_font = &r.legend.label.font;
        let (label_size, label_family) = (label_font.size_or(11.0), label_font.family_or("sans-serif"));
        let title_font = &r.legend.title.font;
        let (title_size, title_family) = (title_font.size_or(12.0), title_font.family_or("sans-serif"));
        // d3's tick target for a continuous legend is fixed at 5 over the raw
        // domain; the labels print comma-grouped at the precision the tick
        // step implies (d3-format's `precisionFixed`).
        let ticks = d3rs::ticks::d3_ticks(domain.0, domain.1, SIZE_LEGEND_TICK_COUNT);
        let step = ticks.get(1).map_or(1.0, |&v| v - ticks[0]);
        let precision = tick_precision(step);
        let labels: Vec<String> = ticks
            .iter()
            .map(|&v| crate::number_locale::d3_format(&format!(",.{precision}f"), v))
            .collect::<Result<_, _>>()?;
        let scaled: Vec<f64> = ticks.iter().map(|&v| size_area(domain, v)).collect();
        let label_ws: Vec<f64> =
            labels.iter().map(|l| fonts.measure(l, label_family, label_size)).collect::<Result<_, _>>()?;
        let glyph = crate::families::scatter_shape::half_extents(symbol, 1.0);
        let (cells, box_h, box_w): (Vec<legend::SizeCell>, f64, f64) = if r.legend.direction == "horizontal" {
            legend::size_legend_row(&scaled, &label_ws, label_size, SIZE_LEGEND_LABEL_OFFSET, glyph)
        } else {
            let grid = size_legend_grid(&scaled, &label_ws, label_size, SIZE_LEGEND_LABEL_OFFSET, glyph);
            let cells = (0..scaled.len())
                .map(|i| legend::SizeCell { y: grid.row_y[i], c: grid.row_c[i], symbol_x: grid.symbol_c, label_x: grid.label_x })
                .collect();
            (cells, grid.box_h, grid.width)
        };
        let title_band = if title.is_empty() { 0.0 } else { title_size + VEGA_LEGEND_TITLE_PADDING };
        let title_w = fonts.measure_shaped(&title, title_family, title_size)?;
        let glyphs = scaled
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
        Ok(Self { title, labels, glyphs, cells, title_band, w: title_w.max(box_w).ceil(), h: title_band + box_h })
    }

    /// Paints at `(x, y)`. Symbol paint is `legend/encode.ts` `symbols()`: an
    /// unfilled mark keeps its interior (stroked in the mark's color unless a
    /// color channel makes the stroke a field ref); beside a color legend the
    /// fill becomes `symbolBaseFillColor` (black) under a transparent base
    /// stroke; with no color channel the mark's own static fill survives at
    /// the mark's opacity.
    fn paint(&self, svg: &mut String, r: &ScatterResolved, x: f64, y: f64) {
        let mark_opacity = r.style.point_mark.opacity.unwrap_or(1.0);
        let symbol_paint = if r.style.point_mark.filled == Some(false) {
            let interior = r.style.point_mark.fill.as_deref().expect("an unfilled mark's interior is checked in render");
            let stroke = if r.color.is_some() {
                String::new()
            } else {
                format!(" stroke=\"{}\"", xml_escape_attr(unfilled_stroke(r)))
            };
            format!("fill=\"{}\"{stroke} stroke-width=\"1.5\" opacity=\"{mark_opacity}\"", xml_escape_attr(interior))
        } else if r.color.is_some() {
            // `symbolStrokeWidth`, Vega's legend default (`vega-parser/src/config.js:200`).
            "fill=\"black\" stroke=\"transparent\" stroke-width=\"1.5\"".to_string()
        } else {
            format!(
                "fill=\"{}\" opacity=\"{mark_opacity}\" stroke-width=\"1.5\"",
                xml_escape_attr(&r.style.single_series_fill)
            )
        };
        let title_font = &r.legend.title.font;
        let title_size = title_font.size_or(12.0);
        if !self.title.is_empty() {
            let _ = write!(
                svg,
                "<text x=\"{x:.2}\" y=\"{:.2}\" font-family=\"{}\" font-size=\"{title_size}\" \
                 font-weight=\"{}\" fill=\"{}\">{}</text>",
                y + vega_fit::baseline_shift("top", title_size),
                vega_escape_attr(title_font.family_or("sans-serif")),
                xml_escape_attr(&title_font.weight_str()),
                xml_escape_attr(title_font.color.as_deref().unwrap_or("#313233")),
                xml_escape(&self.title)
            );
        }
        let label_font = &r.legend.label.font;
        let label_size = label_font.size_or(11.0);
        let entries_y = y + self.title_band;
        for (i, label) in self.labels.iter().enumerate() {
            let cell = &self.cells[i];
            let row_y = entries_y + cell.y;
            let _ = write!(
                svg,
                "<path transform=\"translate({:.2},{:.2})\" d=\"{}\" {symbol_paint}/>",
                x + cell.symbol_x,
                row_y + cell.c,
                self.glyphs[i]
            );
            crate::contract::legend_label_open(svg, "");
            let _ = write!(
                svg,
                "<text x=\"{:.2}\" y=\"{:.2}\" font-family=\"{}\" font-size=\"{label_size}\" \
                 font-weight=\"{}\" fill=\"{}\">{}</text>",
                x + cell.label_x,
                row_y + cell.c + vega_fit::baseline_shift("middle", label_size),
                vega_escape_attr(label_font.family_or("sans-serif")),
                xml_escape_attr(&label_font.weight_str()),
                xml_escape_attr(label_font.color.as_deref().unwrap_or("#626366")),
                xml_escape(label)
            );
            crate::contract::mark_group_close(svg);
        }
    }
}

/// How a shape legend's symbols are painted (`legend/encode.ts` `symbols()`):
/// an unfilled mark keeps its interior; a rule layer's point gives the legend
/// the rules' ink (the first fired rule's: y rules paint before x rules);
/// beside a color channel the fill is a field ref, black; else the mark's own.
fn shape_legend_fill<'a>(r: &'a ScatterResolved, fires: &RuleFires) -> crate::families::scatter_shape::SymbolFill<'a> {
    use crate::families::scatter_shape::SymbolFill;
    if r.style.point_mark.filled == Some(false) {
        return SymbolFill::Ring {
            interior: r.style.point_mark.fill.as_deref().expect("an unfilled mark's interior is checked in render"),
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
fn paint_rule_point(
    svg: &mut String,
    at: (f64, f64),
    (shape_field, aria, layer): (&str, Option<&String>, usize),
    (color, width): (&str, f64),
) {
    let rr = crate::common::symbol_radius(RULE_POINT_SIZE);
    let point_aria = aria.map(|a| format!("{a}; {shape_field}: undefined"));
    let point_label = crate::contract::aria_attr(point_aria.as_deref(), crate::contract::ItemRole::Point);
    if point_aria.is_some() {
        crate::contract::mark_group_open(svg, "symbol", &format!("layer_{layer}_layer_1_marks"));
    }
    let _ = write!(
        svg,
        "<path transform=\"translate({:.4},{:.4})\" \
         d=\"M{rr:.3},0A{rr:.3},{rr:.3},0,1,1,-{rr:.3},0A{rr:.3},{rr:.3},0,1,1,{rr:.3},0\" \
         fill=\"{}\" stroke-width=\"{width}\" opacity=\"1\"{point_label}/>",
        at.0,
        at.1,
        xml_escape_attr(color)
    );
    if point_aria.is_some() {
        crate::contract::mark_group_close(svg);
    }
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
struct LogAxis {
    lo: f64,
    hi: f64,
    ticks: Vec<f64>,
    mantissa_visible: Vec<bool>,
}

impl LogAxis {
    fn new(ax: &AxisStyle, data_min: f64, data_max: f64, count: usize) -> Result<Self, String> {
        if data_min <= 0.0 {
            return Err("scatter: a log axis needs strictly positive data".into());
        }
        let (lo, hi) = match ax.authored_continuous_domain() {
            // The pair reaches Vega verbatim; only an authored `nice` rounds it
            // out to powers of ten.
            Some((lo, hi)) => {
                if lo <= 0.0 || hi <= lo {
                    return Err(format!(
                        "scatter: the authored domain [{lo}, {hi}] on a log axis must be positive and ascending; anything else is not supported"
                    ));
                }
                if data_min < lo || data_max > hi {
                    return Err(format!(
                        "scatter: data [{data_min}, {data_max}] lies outside the authored log domain \
                         [{lo}, {hi}], which this arm does not model"
                    ));
                }
                if ax.scale.get("nice").and_then(Value::as_bool) == Some(true) {
                    crate::families::cartesian::log_nice_domain(lo, hi)
                } else {
                    (lo, hi)
                }
            }
            None => crate::families::cartesian::log_nice_domain(data_min, data_max),
        };
        let ticks = crate::families::cartesian::log_ticks(lo, hi, count);
        let mantissa_visible = crate::families::cartesian::log_tick_mantissa_visible(&ticks, count);
        Ok(Self { lo, hi, ticks, mantissa_visible })
    }

    /// The value's fraction of the plot height from the top.
    fn frac_from_top(&self, v: f64) -> f64 {
        (self.hi.ln() - v.ln()) / (self.hi.ln() - self.lo.ln())
    }

    /// The value's fraction of the plot width from the left.
    fn frac_from_left(&self, v: f64) -> f64 {
        (v.ln() - self.lo.ln()) / (self.hi.ln() - self.lo.ln())
    }
}

/// Which log tick labels survive `labelOverlap: "greedy"`: only the mantissa
/// survivors (non-empty labels) are candidates — a blanked tick never blocks
/// its neighbors. `ink(i)` is label `i`'s bounds at the current plot size.
fn log_label_visible(
    labels: &[String],
    separation: f64,
    ink: impl Fn(usize) -> vega_fit::Bounds,
) -> Vec<bool> {
    let survivors: Vec<usize> = (0..labels.len()).filter(|&i| !labels[i].is_empty()).collect();
    let bounds: Vec<vega_fit::Bounds> = survivors.iter().map(|&i| ink(i)).collect();
    let mut visible = vec![false; labels.len()];
    for (&i, v) in survivors.iter().zip(vega_fit::greedy_visible(&bounds, separation)) {
        visible[i] = v;
    }
    visible
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
        let value = row_str(row, field);
        if !domain.contains(&value) {
            domain.push(value);
        }
    }
    let Some(sort) = sort else {
        let mut by_label: Vec<Value> = Vec::with_capacity(domain.len());
        for category in &domain {
            let row = source.iter().find(|row| row_str(row, field) == *category).expect("domain built from source");
            by_label.push(row.get(field).filter(|v| !v.is_null()).cloned().unwrap_or_else(|| Value::String(String::new())));
        }
        let ordered = crate::common::vega_ascending_sort(by_label).map_err(|e| format!("scatter: {e}"))?;
        return Ok(ordered
            .iter()
            .map(|v| match v {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            })
            .collect());
    };
    let mut keyed: Vec<(String, f64)> = Vec::with_capacity(domain.len());
    for category in domain {
        let mut key: Option<f64> = None;
        for row in &source {
            if row_str(row, field) != category {
                continue;
            }
            // `min` skips nulls, as production's bar sort does.
            if matches!(row.get(&sort.by), None | Some(Value::Null)) {
                continue;
            }
            let Some(value) = row_num(row, &sort.by) else {
                return Err(format!(
                    "scatter: sort by {:?} is not supported — Vega's `min` over \
                     non-numeric values is not ported",
                    sort.by
                ));
            };
            key = Some(key.map_or(value, |k| k.min(value)));
        }
        // A category with no value at all ranks below every number, which
        // Vega's comparator puts first ascending and last descending.
        keyed.push((category, key.unwrap_or(f64::NEG_INFINITY)));
    }
    keyed.sort_by(|a, b| {
        let ord = a.1.total_cmp(&b.1);
        if sort.order == SortDir::Desc {
            ord.reverse()
        } else {
            ord
        }
    });
    Ok(keyed.into_iter().map(|(category, _)| category).collect())
}

/// The point mark's size scale range — Vega-Lite's own fixed default for a
/// quantitative size channel on a `point` mark, probe-confirmed independent
/// of `mark.size`, `mark.type`-implied config, and plot geometry (varied
/// width/height/mark.size, range never moved).
const SIZE_SCALE_RANGE: (f64, f64) = (4.0, 361.0);
/// Vega's own d3 tick target for a continuous (size/opacity/shape) legend —
/// probe-confirmed fixed at 5 regardless of the legend's pixel geometry
/// (unlike an AXIS's own tickCount, which scales with plot size).
const SIZE_LEGEND_TICK_COUNT: usize = 5;
/// `legend.label.padding` (VL's `labelOffset`) as production always
/// compiles it, the size legend's gap from its per-row symbol cell
/// (`size_legend_grid`) to the label.
const SIZE_LEGEND_LABEL_OFFSET: f64 = 8.0;

/// The `(x, y, size)` readings of a row Vega-Lite keeps, `None` for a dropped
/// one. `mark.invalid: "filter"` compiles to one `&&` over every quantitative
/// or temporal field the unit encodes (`filterinvalid.ts:36-55,85-99`): x and
/// y unless categorical, and size. A categorical axis adds no term, so a null
/// category stays (Vega draws it as its own `null` band; `render` refuses
/// it); a date x is kept when it parses, the way `toDate` + `isDate` read it.
/// Every domain, mark and legend reads the rows this keeps.
fn kept_reading(
    row: &serde_json::Map<String, Value>,
    r: &ScatterResolved,
    x_nominal: bool,
    x_dates: bool,
    y_nominal: bool,
) -> Option<(f64, Option<f64>, Option<f64>)> {
    let x = if x_nominal {
        0.0
    } else if x_dates {
        let (year, month, day) = crate::families::temporal::parse_iso_date(&row_str(row, &r.x))?;
        crate::families::temporal::days_from_civil(year, month, day) as f64
    } else {
        row_num(row, &r.x)?
    };
    let y = if y_nominal { None } else { Some(row_num(row, &r.y)?) };
    let size = match r.size.as_deref() {
        Some(size_field) => Some(row_num(row, size_field)?),
        None => None,
    };
    if let Some(color_field) = r.color.as_deref().filter(|_| quantitative_color(r)) {
        row_num(row, color_field)?;
    }
    Some((x, y, size))
}

/// A numeric color column is a continuous scale: Vega-Lite's `blues` ramp
/// over the data extent, with a gradient legend. A wide chart's color is its
/// own nominal series label.
fn quantitative_color(r: &ScatterResolved) -> bool {
    r.wide_measures.is_empty()
        && r.resolved_channels.get("color").is_some_and(|ch| ch.quantitative_data == Some(true))
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

/// The rows `kept_reading` keeps.
fn drawn_rows<'a>(
    rows: &'a [serde_json::Map<String, Value>],
    r: &ScatterResolved,
    x_nominal: bool,
    x_dates: bool,
    y_nominal: bool,
) -> Vec<&'a serde_json::Map<String, Value>> {
    rows.iter()
        .filter(|row| kept_reading(row, r, x_nominal, x_dates, y_nominal).is_some())
        .collect()
}

/// The distinct dates of the drawn rows, ascending. Production labels a date
/// axis from a ladder over every row's dates but scales it to the kept rows,
/// so a date only a dropped row carries would make the two differ; that is
/// refused rather than drawn from the kept rows alone. Production's ladder
/// reads every row's dates (`emitters/_cartesian.py:416-418`,
/// `emitters/_channels.py:1030-1031`); the Vega-Lite filter that drops a row
/// (`filterinvalid.ts:36-55`) runs after it.
fn axis_dates(
    rows: &[serde_json::Map<String, Value>],
    drawn: &[&serde_json::Map<String, Value>],
    x_field: &str,
) -> Result<Vec<String>, String> {
    let mut dates: Vec<String> = drawn.iter().map(|row| row_str(row, x_field)).collect();
    dates.sort();
    dates.dedup();
    let dropped_only = rows
        .iter()
        .map(|row| row_str(row, x_field))
        .any(|date| !date.is_empty() && dates.binary_search(&date).is_err());
    if dropped_only {
        return Err("scatter: a date only dropped rows carry is not supported — production \
                    labels the axis from every row's dates but scales it to the kept rows"
            .into());
    }
    Ok(dates)
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
fn y_zero_rule_fires(ax_y: &AxisStyle, y_data_min: f64, y_data_max: f64) -> bool {
    let zero_in_shared_domain = || -> bool { y_data_min <= 0.0 && 0.0 <= y_data_max };
    let authored_y_domain = ax_y.authored_continuous_domain();
    let excludes_zero =
        authored_y_domain.is_some_and(|(a, b)| !(a.min(b) <= 0.0 && 0.0 <= a.max(b)));
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
    fn of(r: &ScatterResolved, rows: &[serde_json::Map<String, Value>]) -> Self {
        let (ax_x, ax_y) = (&r.style.axis_x, &r.style.axis_y);
        let (y_min, y_max) = column_extent(rows, &r.y);
        let (x_min, x_max) = column_extent(rows, &r.x);
        Self {
            y_zero: y_zero_rule_fires(ax_y, y_min, y_max),
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
) -> Result<(Vec<String>, legend::VegaLegend), String> {
    let title = legend::legend_title_text(r.color.as_deref().unwrap_or(""), &r.legend, &r.field_labels)?;
    let labels = order.iter().map(|&idx| color_domain[idx].as_str());
    let limited = legend::limit_legend_text(labels, &title, &r.legend, fonts)?;
    let symbols: Vec<Option<legend::LegendSymbol>> = match ring_glyphs {
        None => vec![Some(legend::LegendSymbol::FilledCircle); order.len()],
        Some(names) => order
            .iter()
            .map(|&idx| {
                let (x, y) = crate::families::scatter_shape::half_extents(names[idx], 5.0);
                Some(legend::LegendSymbol::Glyph(x, y))
            })
            .collect(),
    };
    let layout = legend::legend_from_widths(
        &limited.label_ws,
        &symbols,
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

/// The x/y kinds and gates `render` classified before dispatching a
/// `multiples` chart to `render_facet`.
struct FacetShape {
    x_dates: bool,
    x_scale_untouched: bool,
    x_nominal: bool,
    y_nominal: bool,
    y_measure: bool,
    labels_on: bool,
}

/// Small multiples: one fit-arm panel per baked partition cell, composed by
/// the shared facet composer. Scope: a date x against a measure y on a right
/// axis, `multiples.scale: "shared"`, a fully populated grid. Every other
/// shape refuses by name.
fn render_facet(payload: &Payload, fonts: &FontBook, r: &ScatterResolved, shape: FacetShape) -> Result<String, Diagnostic> {
    let mc = r.multiples.as_ref().expect("render dispatched on multiples");
    let legend_cfg = &r.legend;
    if shape.x_nominal {
        return Err("scatter: multiples with a category x not supported".into());
    }
    if !shape.x_dates {
        return Err("scatter: multiples with a numeric x not supported".into());
    }
    if shape.y_nominal {
        return Err("scatter: multiples with a categorical y not supported".into());
    }
    if !shape.y_measure {
        return Err("scatter: multiples needs a baked measure y ladder".into());
    }
    if r.style.axis_y.position.as_deref() != Some("right") {
        return Err("scatter: multiples needs a right value axis".into());
    }
    if !shape.x_scale_untouched {
        return Err("scatter: multiples with an authored x scale is not supported".into());
    }
    if shape.labels_on {
        return Err("scatter: multiples with point value labels not supported".into());
    }
    let legend_edge = if (r.color.is_some() || r.size.is_some()) && r.legend.visible {
        facet_compose::facet_legend_edge("scatter", &r.legend.position)?
    } else {
        facet_compose::LegendEdge::Top
    };
    if mc.scale == "independent" {
        return Err("scatter: multiples.scale independent not supported yet — only shared".into());
    }
    let ax_y = &r.style.axis_y;
    if ax_y.domain_min.is_none() || ax_y.domain_max.is_none() || ax_y.authored_continuous_domain().is_some() {
        return Err("scatter: multiples needs the resolver's baked y domain, not an authored one".into());
    }
    let has_mirror = ax_y.mirror_requested("scatter")?;
    if r.panel_axes.is_empty() {
        return Err("scatter: multiples with no baked panel_axes — resolve() should always supply at least one".into());
    }
    let axes = &r.panel_axes;
    let panels = multiples::regroup(axes, &payload.rows).map_err(|e| format!("scatter: {e}"))?;
    let panel_rows_n = multiples::panel_axis_cardinality(axes, mc.rows.as_deref());
    let panel_cols_n = multiples::panel_axis_cardinality(axes, mc.columns.as_deref());
    if panels.len() != panel_rows_n * panel_cols_n {
        return Err("scatter: multiples produced a sparse facet grid — not supported yet, only a fully populated rows x columns grid".into());
    }
    let index_of = |field: &Option<String>| field.as_deref().and_then(|f| axes.iter().position(|a| a.field == f));

    // The date axis is resolved once, against the naive per-panel width the
    // emitter's own crowding pass measured (`RenderBox.width`), and shared by
    // every panel and by the composite's footer.
    let drawn = drawn_rows(&payload.rows, r, shape.x_nominal, shape.x_dates, shape.y_nominal);
    let dates = axis_dates(&payload.rows, &drawn, &r.x)?;
    let usable_ratio = r
        .style
        .label_usable_ratio
        .ok_or("scatter: label_usable_ratio absent (theme required for a date x axis)")?;
    let naive_panel_w = multiples::facet_panel_width(payload.width, panel_cols_n, has_mirror, 0.0);
    let x_axis = TemporalXAxis::resolve(
        &dates,
        &r.style.axis_x,
        naive_panel_w * usable_ratio,
        crate::common::axis_title_text(&r.x, r.x_label.as_deref(), &r.field_labels),
        None,
        fonts,
    )?;
    if let Some(label_count) = x_axis.collision {
        crate::families::cartesian::warn_axis_label_collision(&r.id, &r.x, label_count);
    }

    // A partition field the chart also encodes (`color: series` under
    // `rows: series`) stays a column of its own panel's rows in Vega-Lite;
    // `regroup` strips it, so it is restored from the panel's key.
    let panel_payloads: Vec<Payload> = panels
        .iter()
        .map(|panel| {
            let mut panel_payload = payload.clone();
            panel_payload.rows = panel
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
            panel_payload
        })
        .collect();
    let render_panels = |plot_w: f64, plot_h: f64, value_axis_right_overhang: f64, left_overhang: f64| {
        panel_payloads
            .iter()
            .enumerate()
            .map(|(i, panel_payload)| {
                let opts = ScatterPanelOpts {
                    plot_w,
                    plot_h,
                    shared_rows: &payload.rows,
                    x_axis: &x_axis,
                    paint_value_axis: i % panel_cols_n == panel_cols_n - 1,
                    fold_axis_gutter: panel_cols_n == 1,
                    value_axis_right_overhang,
                    left_overhang,
                    has_mirror,
                };
                let (svg, geometry) = render_fit(panel_payload, fonts, r, false, false, true, &[], Some(&opts))?;
                Ok((svg, geometry.expect("a panel render carries its geometry")))
            })
            .collect::<Result<Vec<_>, String>>()
    };
    // Every cell's group origin is its plot origin, so the composite places
    // one shared plot offset inside every panel canvas: the widest left ink
    // any panel measures (a panel whose marks start further right has less).
    let render_pass = |plot_w: f64, plot_h: f64, value_axis_right_overhang: f64| -> Result<_, Diagnostic> {
        let panels = render_panels(plot_w, plot_h, value_axis_right_overhang, 0.0)?;
        // A sized mark reaching above its panel's plot moves the composite's
        // ceiled height terms by an amount production's single solve reads at
        // another panel height: the two can land a pixel apart.
        if r.size.is_some() && panels.iter().any(|(_, g)| -g.ink.y1 > g.top_axis_overhang + 1e-9) {
            return Err("scatter: multiples with a size channel whose marks reach past the plot top are not supported".into());
        }
        let left_overhang = panels.iter().map(|(_, g)| g.left_mark_overhang).fold(0.0_f64, f64::max);
        if panels.iter().all(|(_, g)| g.left_mark_overhang == left_overhang) {
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
    let color_domain = if quantitative_color(r) {
        Vec::new()
    } else {
        r.color.as_deref().map_or_else(Vec::new, |cf| color_domain_of(drawn.iter().copied(), cf))
    };
    let category_scale = r.color.as_deref().and_then(|cf| crate::common::category_scale_for(&r.category_colors, cf));
    let legend_fills: Vec<String> = color_domain
        .iter()
        .map(|label| crate::common::series_color(label, &color_domain, &r.palette, category_scale, "#ccc"))
        .collect::<Result<_, _>>()?;
    // A shape channel draws its own unconfigured legend on the right, except on
    // the color field, where it merges into the color legend with varying
    // glyphs (a rule layer's point keeps the two apart).
    let fires = RuleFires::of(r, &payload.rows);
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
        })
        .collect();
    let legend_title = match r.color.as_deref() {
        Some(color) => legend::legend_title_text(color, legend_cfg, &r.field_labels)?,
        None => String::new(),
    };
    let vega_legend = (r.color.is_some() && r.legend.visible && !legend_entries.is_empty())
        .then(|| legend::vega_legend(&legend_entries, &legend_title, legend_cfg, fonts))
        .transpose()?;
    // A numeric color is a gradient legend whose bar follows the unit panel.
    let gradient_id = legend_gradient_id(r);
    let gradient_color = match r.color.as_deref().filter(|_| quantitative_color(r) && r.legend.visible) {
        Some(cf) => {
            let (lo, hi) = color_extent(drawn.iter().copied(), cf);
            let scale = ContinuousColor::blues(lo, hi);
            let title = legend::legend_title_text(cf, legend_cfg, &r.field_labels)?;
            // Whatever can fail laying the legend out fails the same at every
            // size, so the per-size closures below cannot.
            GradientAt::measure(legend_cfg, r.color_format.as_ref(), &scale, (&gradient_id, &title), fonts, (1.0, 1.0))?;
            Some((scale, title))
        }
        None => None,
    };
    let size_domain = size_scale_domain(r, &drawn, (shape.x_nominal, shape.x_dates, shape.y_nominal))?;
    let size_legend = match size_domain.filter(|_| r.legend.visible) {
        Some(domain) => {
            let symbol = r.style.point_mark.shape.as_ref().and_then(Value::as_str).filter(|_| r.shape.is_none());
            Some(SizeLegend::new(r, fonts, domain, symbol.unwrap_or("circle"))?)
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
                legend::emit_vega_legend(svg, &legend_entries, vl, legend_cfg, default_symbol, x, y, false)
            }),
        }))
        .chain(gradient_color.as_ref().map(|(scale, title)| facet_compose::LegendChrome {
            edge: legend_edge,
            width: Box::new(|pw, ph| {
                GradientAt::measure(legend_cfg, r.color_format.as_ref(), scale, (&gradient_id, title), fonts, (pw, ph))
                    .expect("validated at build")
                    .0
            }),
            height: Box::new(|ph| {
                GradientAt::measure(legend_cfg, r.color_format.as_ref(), scale, (&gradient_id, title), fonts, (0.0, ph))
                    .expect("validated at build")
                    .1
            }),
            paint: Box::new(|svg, x, y, pw, ph| {
                GradientAt::paint(legend_cfg, r.color_format.as_ref(), scale, (&gradient_id, title), fonts, (pw, ph), (svg, x, y))
                    .expect("validated at build");
            }),
        }))
        .chain(size_legend.as_ref().map(|sl| facet_compose::LegendChrome {
            edge: legend_edge,
            width: Box::new(|_, _| sl.w),
            height: Box::new(|_| sl.h),
            paint: Box::new(|svg, x, y, _, _| sl.paint(svg, r, x, y)),
        }))
        .chain(shape_trail)
        .collect(),
    };
    let spec = facet_compose::FacetSpec {
        family: "scatter",
        panels: &panels,
        panel_rows_n,
        panel_cols_n,
        row_axis_idx: index_of(&mc.rows),
        col_axis_idx: index_of(&mc.columns),
        has_mirror,
        extra_axis_px: 0.0,
        cell_x_axis: false,
        axis_x: &r.style.axis_x,
        axis_y: &r.style.axis_y,
        header_font_family: &header_font_family,
        background: r.background.as_str(),
    };
    facet_compose::compose_facet_with_chrome(payload, fonts, &spec, Some(&chrome), &render_pass)
}

/// One facet panel's overrides of the single-chart fit arm. `payload.rows`
/// carries the panel's own rows; every shared domain (the baseline-rule
/// extents, the color domain) is read off `shared_rows`, the whole chart's, so
/// all panels agree (`multiples.scale: "shared"`). The color domain keeps only
/// the rows `kept_reading` keeps; the rule extents scan them all.
pub(crate) struct ScatterPanelOpts<'a> {
    pub(crate) plot_w: f64,
    pub(crate) plot_h: f64,
    pub(crate) shared_rows: &'a [serde_json::Map<String, Value>],
    /// The date axis, resolved once against the composite's per-panel
    /// crowding width and shared by every panel.
    pub(crate) x_axis: &'a crate::families::scatter_axis::TemporalXAxis,
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
#[allow(clippy::too_many_arguments)]
fn render_fit(
    payload: &Payload,
    fonts: &FontBook,
    r: &ScatterResolved,
    y_nominal: bool,
    x_nominal: bool,
    x_dates: bool,
    wide_series: &[String],
    panel: Option<&ScatterPanelOpts>,
) -> Result<(String, Option<PanelGeometry>), String> {
    let svg_w = payload.width;
    let svg_h = payload.height;
    // A facet panel is laid out by `pad_plot` against its composite's
    // padding, never its own: the card padding belongs to the composite.
    let pad = if panel.is_some() { None } else { payload.padding.as_ref() };
    let domain_rows: &[serde_json::Map<String, Value>] = panel.map_or(&payload.rows, |o| o.shared_rows);
    let pad_l = pad.map(|p| p.left).unwrap_or(0.0);
    let pad_r = pad.map(|p| p.right).unwrap_or(0.0);
    let pad_t = pad.map(|p| p.top).unwrap_or(0.0);
    let pad_b = pad.map(|p| p.bottom).unwrap_or(0.0);
    // The chart's OWN effective background — see `ScatterResolved::background`.
    let bg = r.background.as_str();
    let avail_w = svg_w - pad_l - pad_r;
    let ax_x = &r.style.axis_x;
    let ax_y = &r.style.axis_y;

    // ── Size channel: value check ─────────────────────────────────────────
    // `apply_color_legend(size_enc, chart.legend)` rides the SAME legend
    // config as color: a visible legend draws the size legend, stacked
    // beneath the color legend when one is drawn, alone at the legend
    // origin otherwise; a hidden legend hides both.
    if let Some(size_field) = r.size.as_deref() {
        for row in &payload.rows {
            match row.get(size_field) {
                None | Some(Value::Null) => {}
                Some(_) if row_num(row, size_field).is_some() => {}
                Some(_) => return Err("scatter: size channel value is not numeric".into()),
            }
        }
    }

    // ── BaselineFeature's zero rule, scatter's half (#8241) ───────────────
    // Production's `_insert_zero_rule`: a `datum: 0` rule on the measure y,
    // gated off a rotated (dot-plot) axis by `_y_carries_the_measure`
    // (`chart.style.axis_y.is_quantitative`, the RESOLVED classification —
    // never re-derived from `y_nominal` here, which is this file's own
    // channel-kind heuristic and would answer the same on every swept case
    // but is a different fact). `y_zero_rule_fires` (shared with
    // `render_legacy`, see its own doc comment) carries the rest of the
    // gate. Scatter carries no `layers` (rejected above); its own wide-
    // measure fold runs at the top of `render()`, before this point, so
    // `payload.rows`/`r.y` are already the FOLDED long rows/`WIDE_VALUE_
    // FIELD` on a wide chart — production's shared-domain union (both the
    // zero rule's straddle check and the unity rule's `effective_measure_
    // domain` data fallback) is exactly the base y column's own raw extent
    // over those folded rows (`column_extent`, unconditioned on whether x
    // parsed, matching production's `numeric_column_values` scan), with no
    // separate union step needed.
    let fires = RuleFires::of(r, domain_rows);
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
    let unity_rule_fires = fires.y_unity;

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
    let mut baseline_rule_fracs_x: [Option<f64>; 2] = [None, None];

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
    for row in &payload.rows {
        let Some((xv, yv, sv)) = kept_reading(row, r, x_nominal, x_dates, y_nominal) else {
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
    if kept.is_empty() {
        return Err("scatter: no numeric x/y rows".into());
    }
    // The color and shape scales read the rows Vega keeps over the whole
    // chart (`domain_rows`), not this panel's own `kept`.
    let drawn = drawn_rows(domain_rows, r, x_nominal, x_dates, y_nominal);
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
    let size_domain = size_scale_domain(r, &drawn, (x_nominal, x_dates, y_nominal))?;
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
    // Nominal-x category tilt ladder: bar's own law
    // (`vega_fit::category_label_layout`'s crowding ladder,
    // `vega_fit::vl_x_label_anchor`'s anchor — both already shared
    // functions every cartesian family calls directly) reused verbatim for
    // scatter's point-scale x. Stays flat off the nominal-x arm — the
    // continuous-x arm has no categorical labels to tilt.
    let mut x_cat_angle = 0.0_f64;
    let mut x_cat_align = "center";
    let mut x_cat_baseline = "top";
    let x_title_plain = crate::common::axis_title_text(&r.x, r.x_label.as_deref(), &r.field_labels);
    let facet_grid = multiples::grid_counts(r.multiples.as_ref(), &r.panel_axes);
    let x_title_text = if panel.is_some() {
        crate::common::refuse_wrapping_facet_title("scatter", &x_title_plain, &ax_x.title, svg_w, facet_grid.1, fonts)?;
        x_title_plain.clone()
    } else {
        crate::common::wrapped_axis_title(&r.id, "x_label", &x_title_plain, &ax_x.title.font, svg_w, fonts)?
    };
    // A date x: one UTC time scale over the distinct dates, the labels and
    // gridlines at the shared temporal ladder's openers.
    let temporal_x: Option<crate::families::scatter_axis::TemporalXAxis> = if let Some(opts) = panel {
        Some(opts.x_axis.clone())
    } else if x_dates {
        let dates = axis_dates(&payload.rows, &drawn, &r.x)?;
        let usable_ratio = r
            .style
            .label_usable_ratio
            .ok_or("scatter: label_usable_ratio absent (theme required for a date x axis)")?;
        let axis = crate::families::scatter_axis::TemporalXAxis::resolve(
            &dates,
            ax_x,
            svg_w * usable_ratio,
            x_title_text.clone(),
            panel.is_none().then_some(crate::contract::LabelKey::X),
            fonts,
        )?;
        if let Some(label_count) = axis.collision {
            crate::families::cartesian::warn_axis_label_collision(&r.id, &r.x, label_count);
        }
        Some(axis)
    } else {
        None
    };
    let kept_x_frac: Vec<f64> = if let Some(axis) = &temporal_x {
        x_ticks = axis.tick_fracs();
        kept.iter()
            .map(|&(_, row)| axis.frac(&row_str(row, &r.x)).expect("kept dates parsed above"))
            .collect()
    } else if x_nominal {
        // Production orders only a categorical y (a dot plot) by `chart.sort`.
        let domain = point_scale_domain(&payload.rows, &kept, &r.x, None)?;
        let n = domain.len() as f64;
        // The theme bakes `axis_x.scale.padding: 0` (band/point padding
        // around categorical x values) — unlike y's dot plot, which has no
        // such override and keeps VL's own raw point-scale default of 0.5
        // (`point_scale_domain`'s caller below). Read from the resolved
        // axis rather than hardcoded, since an authored override is legal.
        let padding = ax_x.scale.get("padding").and_then(Value::as_f64).unwrap_or(0.5);
        // d3's `band()` rescale: `step = 1 / max(1, n - 1 + 2·padding)`
        // (a point scale is a band scale with `paddingInner(1)`), then
        // `start += (stop - start - step·(n - 1))·align` with d3's own
        // default `align: 0.5` — never authorable here, so it's baked in.
        // `(padding + i) / denom` (denom = n - 1 + 2·padding, unfloored)
        // is algebraically identical to this whenever the floor never
        // engages, but for a single category at the theme's own
        // `padding: 0` the floor DOES engage (`denom.max(1.0)` clamps a
        // would-be-0 to 1), and that shortcut then gives `band(0) = 0`
        // (left edge) where d3 centers the lone point at `0.5`.
        let denom = (n - 1.0 + 2.0 * padding).max(1.0);
        let step = 1.0 / denom;
        let start_adj = (1.0 - step * (n - 1.0)) * 0.5;
        let band = |i: usize| start_adj + step * i as f64;
        for (i, category) in domain.iter().enumerate() {
            x_ticks.push(band(i));
            x_labels.push(category.clone());
        }
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
            ("center", "top")
        } else {
            vega_fit::vl_x_label_anchor(x_cat_angle, true)
        };
        kept.iter()
            .map(|&(_, row)| {
                let category = row_str(row, &r.x);
                domain.iter().position(|d| *d == category).map(band).ok_or_else(|| {
                    format!("scatter: x value {category:?} is outside the point-scale domain")
                })
            })
            .collect::<Result<_, String>>()?
    } else if ax_x.is_quantitative && ax_x.is_log_scale() {
        let x_tick_count = ax_x.ticks.count.unwrap_or(6.0) as usize;
        let ladder = LogAxis::new(ax_x, x_min, x_max, x_tick_count)?;
        let x_fmt = ax_x
            .labels
            .spec()
            .expect("gated: fit path requires an authored x label format");
        for (i, &tv) in ladder.ticks.iter().enumerate() {
            x_labels.push(if ladder.mantissa_visible[i] {
                crate::number_locale::d3_format(x_fmt, tv).map_err(|e| format!("scatter x label format: {e}"))?
            } else {
                String::new()
            });
            x_ticks.push(ladder.frac_from_left(tv));
        }
        kept.iter().map(|&(xv, _)| ladder.frac_from_left(xv)).collect()
    } else {
        let (x_lo_raw, x_hi_raw) = if ax_x.is_zero_false() {
            (x_min, x_max)
        } else {
            (x_min.min(0.0), x_max.max(0.0))
        };
        if x_hi_raw <= x_lo_raw {
            return Err("scatter: x domain is degenerate (max ≤ min)".into());
        }
        // Production never bakes domain bounds onto axis_x (`build_cartesian_axes`
        // passes them to the y build only), so the niced extent IS the domain.
        let (x_lo, x_hi) = d3rs::ticks::d3_nice(x_lo_raw, x_hi_raw, 10);
        let x_span = x_hi - x_lo;
        if x_zero_rule_fires {
            baseline_rule_fracs_x[0] = Some((0.0 - x_lo) / x_span);
        }
        if x_unity_rule_fires {
            baseline_rule_fracs_x[1] = Some((1.0 - x_lo) / x_span);
        }
        let x_tick_count = ax_x.ticks.count.unwrap_or(6.0) as usize;
        let x_tick_values = d3rs::ticks::d3_ticks(x_lo, x_hi, x_tick_count);
        let x_fmt = ax_x
            .labels
            .spec()
            .expect("gated: fit path requires an authored x label format");
        for &tv in &x_tick_values {
            x_labels.push(
                crate::number_locale::d3_format(x_fmt, tv)
                    .map_err(|e| format!("scatter x label format: {e}"))?,
            );
            x_ticks.push((tv - x_lo) / x_span);
        }
        kept.iter().map(|&(xv, _)| (xv - x_lo) / x_span).collect()
    };
    // Continuous x's own labelFlush behavior (edges anchor start/end, not
    // centered) only applies to that scale; a nominal x's point-scale bands
    // always center their label under the band, at every position including
    // the two edge bands.
    let x_label_flush = !x_nominal;

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
    let mut points_data: Vec<PointDatum> = Vec::new();
    if y_nominal {
        let domain = point_scale_domain(&payload.rows, &kept, &r.y, r.sort.as_ref())?;
        let bands = domain.len() as f64;
        let band = |i: usize| (0.5 + i as f64) / bands;
        for (i, category) in domain.iter().enumerate() {
            y_ticks.push(band(i));
            y_labels.push(category.clone());
        }
        for (idx, &(_, row)) in kept.iter().enumerate() {
            let category = row_str(row, &r.y);
            let i = domain.iter().position(|d| *d == category).ok_or_else(|| {
                format!("scatter: y value {category:?} is outside the point-scale domain")
            })?;
            points_data.push((kept_x_frac[idx], band(i), series_of(row), mark_radii[idx], row));
        }
    } else if ax_y.is_log_scale() {
        let ladder = LogAxis::new(ax_y, y_dmin, y_dmax, 10)?;
        for (i, &tv) in ladder.ticks.iter().enumerate() {
            y_ticks.push(ladder.frac_from_top(tv));
            y_labels.push(if ladder.mantissa_visible[i] {
                format_y_label(
                    tv,
                    ax_y.ruler.as_ref(),
                    ax_y.tick_label.as_ref(),
                    ax_y.labels.format.as_ref(),
                    i,
                    ladder.ticks.len(),
                )?
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
    } else {
        let (y_min, y_max) = match authored_y_domain(ax_y, &kept_y)? {
            Some(pair) => pair,
            None => {
                refuse_reshaped_scale(ax_y, &r.y, "fit")?;
                unauthored_measure_domain(ax_y, y_dmin, y_dmax)
            }
        };
        if y_max == y_min {
            return Err("scatter: y domain is degenerate (max ≤ min)".into());
        }
        let y_span = y_max - y_min;
        if zero_rule_fires {
            baseline_rule_fracs[0] = Some((y_max - 0.0) / y_span);
        }
        if unity_rule_fires {
            baseline_rule_fracs[1] = Some((y_max - 1.0) / y_span);
        }
        let y_eps = y_span.abs() * 1e-9;
        let ladder: Vec<f64> = ax_y
            .tick_values
            .iter()
            .copied()
            .filter(|&tv| tv >= y_min.min(y_max) - y_eps && tv <= y_min.max(y_max) + y_eps)
            .collect();
        for (i, &tv) in ladder.iter().enumerate() {
            y_ticks.push((y_max - tv) / y_span);
            y_labels.push(format_y_label(
                tv,
                ax_y.ruler.as_ref(),
                ax_y.tick_label.as_ref(),
                ax_y.labels.format.as_ref(),
                i,
                ladder.len(),
            )?);
        }
        for (idx, (&(_, row), &yv)) in kept.iter().zip(kept_y.iter()).enumerate() {
            points_data.push((
                kept_x_frac[idx],
                (y_max - yv) / y_span,
                series_of(row),
                mark_radii[idx],
                row,
            ));
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
    let x_label_ws: Vec<f64> = if ax_x.is_quantitative && ax_x.is_log_scale() {
        x_labels
            .iter()
            .map(|lbl| fonts.measure(lbl, x_family, x_font_size))
            .collect::<Result<_, _>>()?
    } else {
        Vec::new()
    };
    // The x label's `labelFlush` anchor at a position on a plot `pw` wide.
    let x_flush_align = |pos: f64, pw: f64| {
        if !x_label_flush {
            "center"
        } else if pos <= 1.0 {
            "left"
        } else if pos >= pw - 1.0 {
            "right"
        } else {
            "center"
        }
    };
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
    let (y_label_off, y_label_align, y_label_anchor, y_label_edge) = if y_nominal || ax_y.labels.align.is_none() {
        let off = y_tick_eff + ax_y.labels.padding.unwrap_or(6.0);
        let widest = y_label_ws.iter().fold(0.0_f64, |acc, &w| acc.max(w)).min(y_limit);
        (off, "left", "start", off + widest)
    } else {
        let mf = crate::common::measure_family_stand_in(y_family);
        let mut max_w = 0.0_f64;
        if ax_y.is_log_scale() {
            max_w = crate::common::estimated_tick_label_gutter(
                fonts,
                y_family,
                y_font_size,
                y_limit,
                ax_y.tick_label.as_ref(),
                ax_y.labels.format.as_ref(),
                ax_y,
                y_dmin,
                y_dmax,
            )? - crate::common::VALUE_AXIS_BREATHING_ROOM;
        }
        let drawn = crate::common::drawn_tick_values(ax_y);
        for (i, &tv) in drawn.iter().enumerate() {
            let lbl = format_y_label(
                tv,
                ax_y.ruler.as_ref(),
                ax_y.tick_label.as_ref(),
                ax_y.labels.format.as_ref(),
                i,
                drawn.len(),
            )?;
            max_w = max_w.max(fonts.measure(&lbl, mf, y_font_size)?);
        }
        let off = y_tick_eff + max_w.min(y_limit) + crate::common::VALUE_AXIS_BREATHING_ROOM;
        (off, "right", "end", off)
    };

    // `axis_y.mirror: true`: a ghost axis on the opposite edge repeating the
    // primary's labels and ticks at the primary's own reserved offset
    // (`mirror_axis.py`); a facet's panels paint theirs through the composer.
    let mirror_active = panel.is_none() && ax_y.mirror_ghost("scatter", false)?.is_some();
    if mirror_active && (y_nominal || x_nominal || ax_y.is_log_scale()) {
        return Err("scatter: axis_y.mirror on a categorical axis or a log y axis is not supported".into());
    }

    // A start-aligned log axis's gutter ends at its widest label Vega keeps: a
    // label `labelOverlap: greedy` hides is drawn at opacity 0 and adds no
    // ink, so the edge depends on the plot size.
    let y_edge_at = |pw: f64, ph: f64| -> f64 {
        if y_label_align != "left" || !ax_y.is_log_scale() {
            return y_label_edge;
        }
        let visible = log_label_visible(&y_labels, ax_y.labels.min_gap.unwrap_or(0.0), |i| {
            vega_fit::text_ink(pw + y_label_off, ph * y_ticks[i], y_label_ws[i].min(y_limit), y_font_size, y_label_align, "middle")
        });
        let widest = y_label_ws.iter().zip(&visible).filter(|(_, &v)| v).fold(0.0_f64, |acc, (&w, _)| acc.max(w.min(y_limit)));
        y_label_off + widest
    };

    // `r.y` is the synthetic `WIDE_VALUE_FIELD` on a wide chart — its own
    // default title is production's joined measure-name list
    // (`wide_measures_title`, mirrors `emitters/scatter.py`'s own
    // `chart.y_label or wide_measures_title(chart.wide_measures)`), never
    // `slug_to_text` on the synthetic field name.
    let y_title_plain = if wide_series.is_empty() {
        crate::common::axis_title_text(&r.y, r.y_label.as_deref(), &r.field_labels)
    } else {
        r.y_label
            .as_deref()
            .map(crate::common::collapse_title_newlines)
            .unwrap_or_else(|| crate::common::wide_measures_title(&r.wide_measures, &r.field_labels))
    };
    let y_title_text = if panel.is_some() {
        crate::common::refuse_wrapping_facet_title("scatter", &y_title_plain, &ax_y.title, svg_h, facet_grid.0, fonts)?;
        y_title_plain.clone()
    } else {
        crate::common::wrapped_axis_title(&r.id, "y_label", &y_title_plain, &ax_y.title.font, svg_h, fonts)?
    };
    let x_title_band = crate::common::axis_title_band(&ax_x.title, &x_title_text);
    let y_title_extra = crate::common::y_title_extra(&ax_y.title, &y_title_text);
    let x_title_w = crate::common::widest_title_line(&ax_x.title, &x_title_text, fonts)?;
    let y_title_w = crate::common::widest_title_line(&ax_y.title, &y_title_text, fonts)?;
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
    let color_domain: Vec<String> = if !wide_series.is_empty() {
        wide_series.to_vec()
    } else if quantitative_color(r) {
        Vec::new()
    } else {
        r.color.as_deref().map_or_else(Vec::new, |cf| color_domain_of(drawn.iter().copied(), cf))
    };
    // The board's plan for this chart's color field, if it binds one
    // (#7167) — the point/legend fill defers to it over the local
    // alphabetical color_domain position.
    let category_scale: Option<&crate::common::CategoryColorScale> =
        r.color.as_deref().and_then(|cf| crate::common::category_scale_for(&r.category_colors, cf));
    // `_dot_plot_roles`: one category axis, one measure axis, a color split.
    let dot_plot = r.color.is_some() && (x_nominal || y_nominal);
    let legend_shown = panel.is_none() && r.color.is_some() && r.legend.visible && !color_domain.is_empty();
    let color_scale = match r.color.as_deref().filter(|_| quantitative_color(r)) {
        Some(cf) => {
            let (lo, hi) = color_extent(drawn.iter().copied(), cf);
            Some(ContinuousColor::blues(lo, hi))
        }
        None => None,
    };
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
    let color_legend = if legend_shown {
        let ring = unfilled.then_some(legend_glyph_names.as_slice());
        Some(color_legend_layout(r, &color_domain, &legend_order, fonts, ring)?)
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
        Some(domain) => Some(SizeLegend::new(r, fonts, domain, mark_symbol.unwrap_or("circle"))?),
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

    // Production's `value_labels` text layer (`_POINT_POS_MAP["top"]`:
    // `baseline: bottom, dy: -4`) draws one label per kept point at the
    // board font; its ink joins the fit like any mark's, so text and measured
    // advance are fixed ahead of the plot rect.
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
    let (value_label_align, value_label_baseline, anchor_dx, anchor_dy) =
        crate::common::value_label_anchor("top").expect("top is a value-label position");
    let value_label_dx = value_labels.and_then(|ml| ml.dx).unwrap_or(anchor_dx);
    let value_label_dy = value_labels.and_then(|ml| ml.dy).unwrap_or(anchor_dy);
    // (text, shaped advance) per point, parallel to `points_data`.
    let mut value_label_texts: Vec<(String, f64)> = Vec::new();
    if let Some(ml) = value_labels {
        for &(_, _, _, _, row) in &points_data {
            let yv = row_num(row, &r.y)
                .ok_or_else(|| "scatter: internal error: value label y missing".to_string())?;
            let text = crate::common::format_mark_label(ml.format.as_ref(), yv, r.style.label_is_house)
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
                // Right label band + rotated y title.
                b.union(&vega_fit::Bounds::new(
                    0.0,
                    0.0,
                    pw + y_edge_at(pw, ph) + y_title_extra,
                    ph,
                ));
                if y_title_extra > 0.0 && panel.is_none() {
                    let x = pw + y_edge_at(pw, ph) + y_title_extra;
                    b.union(&vega_fit::Bounds::new(x, ph / 2.0 - y_title_w / 2.0, x, ph / 2.0 + y_title_w / 2.0));
                }
                let log_visible = ax_y.is_log_scale().then(|| {
                    log_label_visible(&y_labels, ax_y.labels.min_gap.unwrap_or(0.0), |i| {
                        vega_fit::text_ink(
                            pw + y_label_off,
                            ph * y_ticks[i],
                            y_label_ws[i].min(y_limit),
                            y_font_size,
                            y_label_align,
                            "middle",
                        )
                    })
                });
                for (i, (&frac, &lw)) in y_ticks.iter().zip(y_label_ws.iter()).enumerate() {
                    if log_visible.as_ref().is_some_and(|v| !v[i]) {
                        continue;
                    }
                    let mut label = vega_fit::text_ink(
                        pw + y_label_off,
                        ph * frac,
                        lw.min(y_limit),
                        y_font_size,
                        y_label_align,
                        "middle",
                    );
                    // A facet's value axis is the composite's `row_footer`, a
                    // role apart from the cell: a label overhanging its own
                    // row's top or bottom edge never moves the row's marks —
                    // only the gutter's WIDTH reaches the cell.
                    if panel.is_some() {
                        top_overhang.set(top_overhang.get().min(label.y1));
                        label.y1 = label.y1.max(0.0);
                        label.y2 = label.y2.min(ph);
                    }
                    b.union(&label);
                    if mirror_active {
                        b.union(&vega_fit::text_ink(-y_label_off, ph * frac, lw.min(y_limit), y_font_size, "right", "middle"));
                    }
                }
                // X labels: flush anchors at the plot edges on a continuous
                // scale (labelFlush default true); a nominal scale's bands
                // always center, edges included — tilted per the ladder
                // above (`x_cat_angle`), reusing bar's own rotated-ink law
                // (`common::rotated_category_label_ink`).
                let row_y = ph + x_axis_offset + x_tick_eff + x_label_pad;
                // The x axis's own bounds: Vega's `yBounds` beyond the plot
                // rect (`ViewLayout.js:62-65`), read by a bottom legend.
                let mut x_axis = vega_fit::Bounds::new(0.0, 0.0, pw, ph);
                // A facet draws its shared x axis once, below the last row:
                // no panel reserves ink for it.
                if panel.is_none() {
                    if let Some(axis) = &temporal_x {
                        axis.union_label_ink(pw, ph, &mut x_axis);
                    }
                }
                let x_log_visible = (ax_x.is_quantitative && ax_x.is_log_scale()).then(|| {
                    log_label_visible(&x_labels, ax_x.labels.min_gap.unwrap_or(0.0), |i| {
                        let pos = x_ticks[i] * pw;
                        vega_fit::text_ink(
                            pos,
                            row_y,
                            x_label_ws[i].min(x_limit),
                            x_font_size,
                            x_flush_align(pos, pw),
                            "top",
                        )
                    })
                });
                for (i, (&frac, lbl)) in
                    x_ticks.iter().zip(x_labels.iter()).enumerate().filter(|_| panel.is_none())
                {
                    if x_log_visible.as_ref().is_some_and(|v| !v[i]) {
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
                            false,
                        ));
                        continue;
                    }
                    let align = if !x_label_flush {
                        "center"
                    } else if pos <= 1.0 {
                        "left"
                    } else if pos >= pw - 1.0 {
                        "right"
                    } else {
                        "center"
                    };
                    x_axis.union(&vega_fit::text_ink(
                        pos,
                        row_y,
                        w.min(x_limit),
                        x_font_size,
                        align,
                        "top",
                    ));
                }
                // Outermost drawn tick strokes.
                if x_ticks_visible && panel.is_none() {
                    let tw = ax_x.ticks.width.unwrap_or(1.0);
                    if let (Some(&first), Some(&last)) = (x_ticks.first(), x_ticks.last()) {
                        for frac in [first, last] {
                            let pos = frac * pw;
                            x_axis.union(&vega_fit::Bounds::new(pos, ph, pos, ph).expand(tw));
                        }
                    }
                }
                b.union(&x_axis);
                if x_title_extra > 0.0 && panel.is_none() {
                    let ext = (b.y2 - ph).max(0.0);
                    x_axis.union(&vega_fit::Bounds::new(pw / 2.0 - x_title_w / 2.0, 0.0, pw / 2.0 + x_title_w / 2.0, ph + ext + x_title_extra));
                    b.union(&x_axis);
                }
                // Symbol ink: every point expanded by its own circle radius
                // and, when unfilled, its stroke (a filled point has no stroke
                // color, so boundStroke adds none).
                for &(x_frac, y_frac, _, pr, _) in &points_data {
                    let px = x_frac * pw;
                    let py = ph * y_frac;
                    b.union(&vega_fit::Bounds::new(px, py, px, py).expand(pr + symbol_stroke_ink));
                }
                for (&(x_frac, y_frac, _, _, _), (_, w)) in points_data.iter().zip(&value_label_texts) {
                    b.union(&vega_fit::text_ink(
                        x_frac * pw + value_label_dx,
                        ph * y_frac + value_label_dy,
                        *w,
                        value_label_size,
                        value_label_align,
                        value_label_baseline,
                    ));
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
                        let ry = ph * frac;
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
                    for frac in baseline_rule_fracs_x.into_iter().flatten() {
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
                // Legend: a right one (with the size and shape legends
                // stacked under it) at the view ink's right edge + offset;
                // any other edge through `legendParams`, the y axis sitting
                // right so `xBounds.x1` is the plot's own 0.
                let lx = pw + y_edge_at(pw, ph) + y_title_extra + VEGA_LEGEND_OFFSET;
                let color_block = if legend_shown { Some((legend_w, legend_h)) } else { gradient_block(pw, ph)? };
                let stack = legend_stack(color_block);
                if edge_stack && !right_strip {
                    let origin = legend::legend_origin(&r.legend.position, (0.0, pw), (0.0, x_axis.y2), (pw, ph), stack.size);
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
        None => vega_fit::fit_plot(svg_w, svg_h, (pad_l, pad_r, pad_t, pad_b), &mut ink_pass, title_ink.as_ref()),
    };
    if let Some(e) = fit_err {
        return Err(e);
    }
    if m.plot_w <= 0.0 || m.plot_h <= 0.0 {
        return Err(format!("scatter: plot box too small ({}×{})", m.plot_w, m.plot_h));
    }
    let (plot_w, plot_h) = (m.plot_w, m.plot_h);
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
    let mut svg = String::with_capacity(16384);
    // A panel's canvas is its plot plus the ink margins the fit measured.
    let (canvas_w, canvas_h) = match panel {
        Some(_) => (left_margin + plot_w + m.right, top_margin + plot_h),
        None => (svg_w, svg_h),
    };
    ChartFrame::new(canvas_w, canvas_h, (pad_l, pad_r, pad_t, pad_b), bg, py_float_str).open(&mut svg);
    let content_start = svg.len();

    if let (Some((tx, ty)), Some(plan)) = (m.title_pos, &title_plan) {
        plan.paint(&r.title_source(), &mut svg, pad_l + left_margin + tx, pad_t + top_margin + ty);
    }

    let group_x = pad_l + left_margin;
    let group_y = pad_t + top_margin;
    let _ = write!(svg, "<g transform=\"translate({group_x:.3},{group_y:.3})\">");

    let x_pos = |frac: f64| frac * plot_w;
    let y_pos = |frac: f64| plot_h * frac;

    // Gridlines — grid/tick positions round to whole pixels (Vega
    // tickRound), then take the axis group's +0.5 crispness shift; labels
    // keep exact positions.
    if ax_x.grid.visible.unwrap_or(false) {
        let gc = crate::common::required_ink(ax_x.grid.color.as_deref(), "scatter", "axis_x.grid.color")?;
        let gw = ax_x.grid.width.unwrap_or(1.0);
        let go = ax_x.grid.opacity.unwrap_or(1.0);
        for &frac in &x_ticks {
            let gx = x_pos(frac).round() + 0.5;
            let _ = write!(
                svg,
                "<line x1=\"{gx:.3}\" y1=\"0.5\" x2=\"{gx:.3}\" y2=\"{:.3}\" \
                 stroke=\"{gc}\" stroke-width=\"{gw}\" opacity=\"{go}\"/>",
                plot_h + 0.5, gc = xml_escape_attr(gc)
            );
        }
    }
    if ax_y.grid.visible.unwrap_or(false) {
        let gc = crate::common::required_ink(ax_y.grid.color.as_deref(), "scatter", "axis_y.grid.color")?;
        let gw = ax_y.grid.width.unwrap_or(1.0);
        let go = ax_y.grid.opacity.unwrap_or(1.0);
        for &frac in &y_ticks {
            let gy = y_pos(frac).round() + 0.5;
            let _ = write!(
                svg,
                "<line x1=\"0.5\" y1=\"{gy:.3}\" x2=\"{:.3}\" y2=\"{gy:.3}\" \
                 stroke=\"{gc}\" stroke-width=\"{gw}\" opacity=\"{go}\"/>",
                plot_w + 0.5, gc = xml_escape_attr(gc)
            );
        }
    }

    // Points.
    let color_for = |series: &str| -> Result<String, String> {
        if color_domain.is_empty() {
            return Ok(r.style.single_series_fill.clone());
        }
        crate::common::series_color(series, &color_domain, &r.palette, category_scale, "#ccc")
    };
    let sw_attr = if mark_sw > 0.0 { format!(" stroke-width=\"{mark_sw}\"") } else { String::new() };
    // Front-end contract: `_scatter_roles`, ported. y (dependent) leads, x
    // (independent) follows; a bound color channel promotes to a swatched
    // header. `xv` here is already the row's raw x reading (not the scaled
    // position) -- reused directly rather than re-reading the row.
    let y_axis_title = y_title_plain.clone();
    let x_axis_title = crate::common::axis_title_text(&r.x, r.x_label.as_deref(), &r.field_labels);
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
            crate::contract::paint_item(
                &mut svg, "rule", &rule_layer_name, crate::contract::ItemRole::RuleMark, crate::contract::Reads::Hidden,
                |aria| format!(
                    "<line x1=\"0.000\" y1=\"{gy:.3}\" x2=\"{plot_w:.3}\" y2=\"{gy:.3}\" \
                     stroke=\"{zc}\" stroke-width=\"{zw}\"{aria}/>", zc = xml_escape_attr(zc)
                ),
            );
            if let Some(shape_field) = r.shape.as_deref().filter(|_| rule_shape) {
                let rule_value = if i == 0 { 0.0 } else { 1.0 };
                let aria = rule_aria(tooltip_labels, &r.style.tooltip_format.spec, &y_axis_title, rule_value)?;
                paint_rule_point(&mut svg, (0.0, gy), (shape_field, aria.as_ref(), fired_rule_count), (zc, zw));
            }
            fired_rule_count += 1;
        }
    }
    if let Some(zx) = ax_x.grid.threshold.as_ref() {
        let zc = zx.color.as_deref().unwrap_or("#8A8C8F");
        let zw = zx.width.unwrap_or(2.0);
        for (i, frac) in baseline_rule_fracs_x.iter().enumerate() {
            let Some(frac) = frac else { continue };
            let gx = x_pos(*frac);
            let layer = fired_rule_count;
            let rule_layer_name = if rule_shape {
                format!("layer_{layer}_layer_0_marks")
            } else {
                format!("layer_{layer}_marks")
            };
            fired_rule_count += 1;
            crate::contract::paint_item(
                &mut svg, "rule", &rule_layer_name, crate::contract::ItemRole::RuleMark, crate::contract::Reads::Hidden,
                |aria| format!(
                    "<line x1=\"{gx:.3}\" y1=\"0.000\" x2=\"{gx:.3}\" y2=\"{plot_h:.3}\" \
                     stroke=\"{zc}\" stroke-width=\"{zw}\"{aria}/>", zc = xml_escape_attr(zc)
                ),
            );
            if let Some(shape_field) = r.shape.as_deref().filter(|_| rule_shape) {
                let rule_value = if i == 0 { 0.0 } else { 1.0 };
                let aria = rule_aria(tooltip_labels, &r.style.tooltip_format.spec, &x_axis_title, rule_value)?;
                paint_rule_point(&mut svg, (gx, 0.0), (shape_field, aria.as_ref(), layer), (zc, zw));
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

    let ghosts_on = panel.is_some_and(|o| o.has_mirror) && tooltip_labels;
    let ghost_ink = if ghosts_on {
        r.style.axis_y.labels.font.color.clone().ok_or("scatter: axis_y.labels.font.color absent (theme required)")?
    } else {
        String::new()
    };
    let mut ghost_points: Vec<(String, f64, f64, String)> = Vec::new();
    let mut value_label_marks: Vec<(f64, f64, Option<String>)> = Vec::new();
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
        let (fill, sw_attr) = if unfilled {
            let stroke = if r.color.is_some() { channel_ink.as_str() } else { unfilled_stroke(r) };
            (
                r.style.point_mark.fill.clone().expect("an unfilled mark's interior is checked in render"),
                format!(" stroke=\"{}\" stroke-width=\"{mark_sw}\"", xml_escape_attr(stroke)),
            )
        } else {
            (channel_ink.clone(), sw_attr.clone())
        };
        let aria = if tooltip_labels && dot_plot {
            let (category_field, measure_field, value_title) =
                if y_nominal { (&r.y, &r.x, &x_axis_title) } else { (&r.x, &r.y, &y_axis_title) };
            let series_field = r.color.as_deref().expect("dot_plot implies a color channel");
            let value = row_num(row, measure_field)
                .ok_or_else(|| "scatter: internal error — tooltip measure value missing".to_string())?;
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
                Some(axis) => axis.tooltip_date(&row_str(row, &r.x)),
                None => row_str(row, &r.x),
            };
            let yv = row_num(row, &r.y)
                .ok_or_else(|| "scatter: internal error: tooltip y value missing".to_string())?;
            let mut rows = vec![
                (x_axis_title.as_str(), x_value),
                (y_axis_title.as_str(), r.style.tooltip_format.paint(yv).map_err(|e| format!("scatter: tooltip value {e}"))?),
            ];
            if let Some(cf) = r.color.as_deref() {
                rows.push((color_title.as_str(), row_str(row, cf)));
            }
            Some(crate::contract::native_label(&rows))
        } else if tooltip_labels {
            let header = r.color.as_deref().map(|cf| row_str(row, cf));
            let y_nominal_str;
            let y_value = if y_nominal {
                y_nominal_str = row_str(row, &r.y);
                crate::contract::ScatterValue::Nominal(&y_nominal_str)
            } else {
                let yv = row_num(row, &r.y)
                    .ok_or_else(|| "scatter: internal error: tooltip y value missing".to_string())?;
                crate::contract::ScatterValue::Quantitative(yv, &r.style.tooltip_format)
            };
            let x_nominal_str;
            let x_value = if let Some(axis) = &temporal_x {
                x_nominal_str = axis.tooltip_date(&row_str(row, &r.x));
                crate::contract::ScatterValue::Nominal(&x_nominal_str)
            } else if x_nominal {
                x_nominal_str = row_str(row, &r.x);
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
            // at all, the measure composed like every other affix. The entry
            // gate above refuses `wide_measures` on a quantitative x outright,
            // so x is always nominal here and carries no format.
            let x_str = row_str(row, &r.x);
            let yv = row_num(row, &r.y)
                .ok_or_else(|| "scatter: internal error: tooltip y value missing".to_string())?;
            let ys = r.style.tooltip_format.paint(yv).map_err(|e| format!("scatter: tooltip value {e}"))?;
            Some(format!("{x_axis_title}: {x_str}; {y_axis_title}: {ys}; Series: {series}"))
        } else {
            None
        };
        let label_attr = crate::contract::aria_attr(aria.as_deref(), crate::contract::ItemRole::Point);
        if aria.is_some() {
            crate::contract::mark_group_open(&mut svg, "symbol", &points_layer_name);
        }
        let d = point_glyph(r, row, &shape_domain, *mark_r)?;
        let _ = write!(
            svg,
            "<path transform=\"translate({cx:.4},{cy:.4})\" d=\"{d}\" \
             fill=\"{fill}\"{sw_attr} opacity=\"{mark_opacity}\"{label_attr}/>", fill = xml_escape_attr(&fill)
        );
        if aria.is_some() {
            crate::contract::mark_group_close(&mut svg);
        }
        if value_labels.is_some() {
            value_label_marks.push((cx, cy, aria.clone()));
        }
        if let (true, Some(label)) = (ghosts_on, &aria) {
            let stroke = if r.color.is_some() { channel_ink.clone() } else { ghost_ink.clone() };
            ghost_points.push((label.clone(), cx, cy, stroke));
        }
    }
    if !value_label_marks.is_empty() {
        let color = value_label_font.and_then(|f| f.color.as_deref()).unwrap_or("#313233");
        let weight = value_label_font.map(|f| f.weight_str()).unwrap_or_else(|| "400".into());
        let font_style = value_label_font.map(crate::common::Font::style_attr).unwrap_or_default();
        let baseline = value_label_dy + vega_fit::baseline_shift(value_label_baseline, value_label_size);
        if tooltip_labels {
            crate::contract::value_label_group_open(&mut svg, &format!("layer_{}_marks", fired_rule_count + 1));
        }
        for ((cx, cy, aria), (text, _)) in value_label_marks.iter().zip(&value_label_texts) {
            let label_attr = crate::contract::aria_attr(aria.as_deref(), crate::contract::ItemRole::TextMark);
            crate::common::write_value_label(
                &mut svg,
                cx + value_label_dx,
                cy + baseline,
                value_label_align,
                value_label_family,
                value_label_size,
                &weight,
                &font_style,
                color,
                &label_attr,
                text,
            );
        }
        if tooltip_labels {
            crate::contract::mark_group_close(&mut svg);
        }
    }
    if ghosts_on {
        let rules: Vec<crate::contract::GhostRule> = ghost_points
            .iter()
            .map(|(aria, x, y, stroke)| crate::contract::GhostRule { aria: Some(aria), x: *x, y: *y, stroke })
            .collect();
        crate::contract::mirror_ghost_layer(&mut svg, &rules, plot_h);
    }

    // X ticks + labels.
    let x_axis_drop = x_axis_offset + 0.5;
    if let Some(axis) = temporal_x.as_ref().filter(|_| panel.is_none()) {
        axis.paint(&mut svg, ax_x, 0.0, plot_w, plot_h)?;
    }
    if temporal_x.is_none() {
        crate::contract::axis_group_open(&mut svg, crate::contract::AxisChannel::X, (0.5, plot_h + x_axis_drop));
    }
    if x_ticks_visible && temporal_x.is_none() {
        let tc = crate::common::required_ink(ax_x.ticks.color.as_deref(), "scatter", "axis_x.ticks.color")?;
        let tw = ax_x.ticks.width.unwrap_or(1.0);
        for &frac in &x_ticks {
            let gx = x_pos(frac).round() + 0.5;
            let _ = write!(
                svg,
                "<line x1=\"{gx:.3}\" y1=\"{:.3}\" x2=\"{gx:.3}\" y2=\"{:.3}\" \
                 stroke=\"{tc}\" stroke-width=\"{tw}\"/>",
                plot_h + x_axis_drop,
                plot_h + x_axis_drop + x_tick_len, tc = xml_escape_attr(tc)
            );
        }
    }
    let x_label_color = ax_x.labels.font.color.as_deref().unwrap_or("#626366");
    let x_label_weight = ax_x.labels.font.weight_str();
    if x_cat_angle != 0.0 {
        // Vega's SVG text transform for a rotated item: translate to the
        // anchor, rotate, then the baseline shift — bar's own
        // `BandXAxis::paint` rotated arm (`bar.rs`), reused verbatim.
        let anchor_y = plot_h + x_axis_drop + x_tick_eff + x_label_pad;
        let dy = vega_fit::baseline_shift(x_cat_baseline, x_font_size);
        let text_anchor = match x_cat_align {
            "left" => "start",
            "right" => "end",
            _ => "middle",
        };
        for (&frac, lbl) in x_ticks.iter().zip(x_labels.iter()) {
            let pos = x_pos(frac) + 0.5;
            let _ = write!(
                svg,
                "<text text-anchor=\"{text_anchor}\" transform=\"translate({pos:.3},{anchor_y:.3}) \
                 rotate({x_cat_angle}) translate(0,{dy:.3})\" \
                 font-family=\"{}\" font-size=\"{x_font_size}\" \
                 font-weight=\"{x_label_weight}\" fill=\"{x_label_color}\">{}</text>",
                vega_escape_attr(x_family),
                xml_escape(lbl), text_anchor = xml_escape_attr(text_anchor), x_label_weight = xml_escape_attr(&x_label_weight), x_label_color = xml_escape_attr(x_label_color)
            );
        }
    } else {
        let x_label_y = plot_h
            + x_axis_drop
            + x_tick_eff
            + x_label_pad
            + vega_fit::baseline_shift("top", x_font_size);
        let x_log_visible = (ax_x.is_quantitative && ax_x.is_log_scale()).then(|| {
            log_label_visible(&x_labels, ax_x.labels.min_gap.unwrap_or(0.0), |i| {
                let pos = x_ticks[i] * plot_w;
                vega_fit::text_ink(
                    pos,
                    plot_h + x_axis_drop + x_tick_eff + x_label_pad,
                    x_label_ws[i].min(x_limit),
                    x_font_size,
                    x_flush_align(pos, plot_w),
                    "top",
                )
            })
        });
        for (i, (&frac, lbl)) in x_ticks.iter().zip(x_labels.iter()).enumerate() {
            if x_log_visible.as_ref().is_some_and(|v| !v[i]) {
                continue;
            }
            let pos = x_pos(frac) + 0.5;
            let anchor = if !x_label_flush {
                "middle"
            } else if pos - 0.5 <= 1.0 {
                "start"
            } else if pos - 0.5 >= plot_w - 1.0 {
                "end"
            } else {
                "middle"
            };
            let _ = write!(
                svg,
                "<text x=\"{pos:.3}\" y=\"{x_label_y:.3}\" text-anchor=\"{anchor}\" \
                 font-family=\"{}\" font-size=\"{x_font_size}\" \
                 font-weight=\"{x_label_weight}\" fill=\"{x_label_color}\">{}</text>",
                vega_escape_attr(x_family),
                xml_escape(lbl), anchor = xml_escape_attr(anchor), x_label_weight = xml_escape_attr(&x_label_weight), x_label_color = xml_escape_attr(x_label_color)
            );
        }
    }

    if x_cat_angle != 0.0 && x_title_band > 0.0 {
        // Vega's `axisTitleLayout` offsets the title by the label ink's reach past
        // the pivot plus `titlePadding`, read from `rotated_category_label_ink`.
        let anchor_y = plot_h + x_axis_drop + x_tick_eff + x_label_pad;
        let reach = x_labels.iter().try_fold(0.0_f64, |acc, lbl| {
            let w = fonts.measure_shaped(lbl, x_family, x_font_size)?.min(x_limit);
            let ink = crate::common::rotated_category_label_ink(
                0.0, 0.0, w, x_font_size, x_cat_align, x_cat_baseline, x_cat_angle, false,
            );
            Ok::<f64, String>(acc.max(ink.y2))
        })?;
        let title_fs = ax_x.title.font.size_or(11.0);
        let title_padding = ax_x.title.padding.unwrap_or(10.0);
        let title_y = anchor_y + reach + title_padding + vega_fit::baseline_shift("top", title_fs);
        crate::common::emit_axis_title_at(&mut svg, &x_title_text, &ax_x.title, plot_w / 2.0, title_y, panel.is_none().then_some(crate::contract::LabelKey::X));
    } else if temporal_x.is_none() {
        let x_label_baseline = plot_h
            + x_axis_drop
            + x_tick_eff
            + x_label_pad
            + vega_fit::baseline_shift("top", x_font_size);
        crate::common::emit_axis_title_bottom(
            &mut svg,
            &x_title_text,
            &ax_x.title,
            plot_w / 2.0,
            x_label_baseline,
            x_font_size,
            panel.is_none().then_some(crate::contract::LabelKey::X),
        );
    }
    if temporal_x.is_none() {
        crate::contract::axis_group_close(&mut svg);
    }

    // The value axis: drawn by the panel that owns it (a facet's last
    // column), anchored `value_axis_dx` past the plot edge.
    let paint_value_axis = panel.is_none_or(|o| o.paint_value_axis);
    let value_axis_dx = panel.map_or(0.0, |o| o.value_axis_right_overhang);
    if paint_value_axis {
        crate::contract::axis_group_open(&mut svg, crate::contract::AxisChannel::Y, (plot_w + value_axis_dx + 0.5, 0.0));
    }
    // Y tick marks: 6px lines between the plot edge and the label gutter.
    if y_tick_eff > 0.0 && paint_value_axis {
        let tc = crate::common::required_ink(ax_y.ticks.color.as_deref(), "scatter", "axis_y.ticks.color")?;
        let tw = ax_y.ticks.width.unwrap_or(1.0);
        for &frac in &y_ticks {
            let gy = y_pos(frac).round() + 0.5;
            let _ = write!(
                svg,
                "<line x1=\"{:.3}\" y1=\"{gy:.3}\" x2=\"{:.3}\" y2=\"{gy:.3}\" \
                 stroke=\"{tc}\" stroke-width=\"{tw}\"/>",
                plot_w + value_axis_dx + 0.5,
                plot_w + value_axis_dx + 0.5 + y_tick_eff, tc = xml_escape_attr(tc)
            );
        }
    }

    // Y labels: right-aligned at the gutter's outer edge, middle baseline.
    let y_label_color = ax_y.labels.font.color.as_deref().unwrap_or("#626366");
    let y_label_weight = ax_y.labels.font.weight_str();
    let y_label_x = plot_w + value_axis_dx + 0.5 + y_label_off;
    let log_visible = ax_y.is_log_scale().then(|| {
        log_label_visible(&y_labels, ax_y.labels.min_gap.unwrap_or(0.0), |i| {
            vega_fit::text_ink(
                plot_w + value_axis_dx + y_label_off,
                plot_h * y_ticks[i],
                y_label_ws[i].min(y_limit),
                y_font_size,
                y_label_align,
                "middle",
            )
        })
    });
    for (i, (&frac, lbl)) in y_ticks.iter().zip(y_labels.iter()).enumerate().filter(|_| paint_value_axis) {
        if log_visible.as_ref().is_some_and(|v| !v[i]) {
            continue;
        }
        let gy = y_pos(frac) + 0.5 + vega_fit::baseline_shift("middle", y_font_size);
        let _ = write!(
            svg,
            "<text x=\"{y_label_x:.3}\" y=\"{gy:.3}\" text-anchor=\"{y_label_anchor}\" \
             font-family=\"{}\" font-size=\"{y_font_size}\" \
             font-weight=\"{y_label_weight}\" fill=\"{y_label_color}\">{}</text>",
            vega_escape_attr(y_family),
            xml_escape(lbl), y_label_anchor = xml_escape_attr(y_label_anchor), y_label_weight = xml_escape_attr(&y_label_weight), y_label_color = xml_escape_attr(y_label_color)
        );
    }

    if paint_value_axis {
        crate::common::emit_axis_title_rotated(
            &mut svg,
            &y_title_text,
            &ax_y.title,
            plot_w + value_axis_dx + 0.5 + y_edge_at(plot_w, plot_h),
            plot_h / 2.0,
            1.0,
            panel.is_none().then_some(crate::contract::LabelKey::Y),
        );
        crate::contract::axis_group_close(&mut svg);
    }
    if mirror_active {
        // The ghost is `dict(primary_axis)` but for `orient`/`grid`/`title`, so
        // its ticks and labels repeat the primary's, sign-flipped onto the
        // left edge's own 0.5 crisp anchor.
        crate::contract::axis_group_open(&mut svg, crate::contract::AxisChannel::Y, (0.5, 0.0));
        if y_tick_eff > 0.0 {
            let tc = crate::common::required_ink(ax_y.ticks.color.as_deref(), "scatter", "axis_y.ticks.color")?;
            let tw = ax_y.ticks.width.unwrap_or(1.0);
            for &frac in &y_ticks {
                let gy = y_pos(frac).round() + 0.5;
                let _ = write!(
                    svg,
                    "<line x1=\"0.500\" y1=\"{gy:.3}\" x2=\"{:.3}\" y2=\"{gy:.3}\" stroke=\"{tc}\" stroke-width=\"{tw}\"/>",
                    0.5 - y_tick_eff,
                    tc = xml_escape_attr(tc)
                );
            }
        }
        for (&frac, lbl) in y_ticks.iter().zip(y_labels.iter()) {
            let gy = y_pos(frac) + 0.5 + vega_fit::baseline_shift("middle", y_font_size);
            let _ = write!(
                svg,
                "<text x=\"{:.3}\" y=\"{gy:.3}\" text-anchor=\"end\" font-family=\"{}\" font-size=\"{y_font_size}\" \
                 font-weight=\"{y_label_weight}\" fill=\"{y_label_color}\">{}</text>",
                0.5 - y_label_off,
                vega_escape_attr(y_family),
                xml_escape(lbl),
                y_label_weight = xml_escape_attr(&y_label_weight),
                y_label_color = xml_escape_attr(y_label_color)
            );
        }
        crate::contract::axis_group_close(&mut svg);
    }

    // Legend. The group's x is `ceil(x2) + 18`, `x2` the y-axis chrome's
    // right edge — `layoutAxis` translates an axis's bounds by its POSITION
    // only, without the axis group's own +0.5 crisp translate (the label's
    // drawn x carries that shift; this quantity does not), so `x2` is the
    // plot rect's own width plus the label gutter and title band, unshifted.
    // Vega ceils this integer AFTER the fit resolves, a separate rounding
    // rule from the exact-float ink union that negotiated the fit itself.
    let x2 = plot_w + y_edge_at(plot_w, plot_h) + y_title_extra;
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
    if let Some((labels, lg)) = &color_legend {
        let fills = color_domain
            .iter()
            .map(|label| crate::common::series_color(label, &color_domain, &r.palette, category_scale, "#ccc"))
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
                    None => legend::LegendSymbol::FilledGlyph(&glyphs[idx]),
                }),
                series_key: Some(&color_domain[idx]),
            })
            .collect();
        let (x, y) = (base.0 + color_off.0 + slide.0, base.1 + color_off.1 + slide.1);
        legend::emit_vega_legend(
            &mut svg,
            &entries,
            lg,
            &r.legend,
            legend::LegendSymbol::FilledCircle,
            x,
            y,
            stamp_legend_series,
        );
    }

    if let (Some(scale), Some(title)) = (&color_scale, &gradient_title) {
        let at = (base.0 + color_off.0 + slide.0, base.1 + color_off.1 + slide.1);
        GradientAt::paint(&r.legend, r.color_format.as_ref(), scale, (&gradient_id, title), fonts, (plot_w, plot_h), (&mut svg, at.0, at.1))?;
    }

    if let Some(sl) = &shape_legend {
        let fill = shape_legend_fill(r, &fires);
        sl.paint(&mut svg, shape_x + slide.0, shape_y + slide.1, fill, mark_opacity);
    }

    if let Some(sl) = &size_legend {
        sl.paint(&mut svg, r, base.0 + size_off.0 + slide.0, base.1 + size_off.1 + slide.1);
    }
    svg.push_str("</g>");
    let svg_end = svg.len();
    svg.push_str("</svg>");

    // The panel's ink in its own canvas frame: the plot rect, every mark's
    // reach past it, and — on a single-column facet — the value-axis gutter.
    let geometry = panel.map(|o| {
        let (plot_left, plot_top) = (pad_l + left_margin, pad_t + top_margin);
        let (plot_right, plot_bottom) = (plot_left + plot_w, plot_top + plot_h);
        let mut ink = vega_fit::Bounds::new(plot_left, plot_top, plot_right, plot_bottom);
        for &(x_frac, y_frac, _, mark_r, _) in &points_data {
            let (px, py) = (plot_left + x_pos(x_frac), plot_top + y_pos(y_frac));
            ink.union(&vega_fit::Bounds::new(px, py, px, py).expand(mark_r + symbol_stroke_ink));
        }
        // The cell's x gridlines run down to `axis.offset` below the plot
        // (`bboxFull` counts a cell's axis-grid child), whatever their stroke.
        if ax_x.grid.visible.unwrap_or(false) {
            for &frac in &x_ticks {
                let gx = plot_left + x_pos(frac);
                ink.union(&vega_fit::Bounds::new(gx, plot_top, gx, plot_bottom + x_axis_offset));
            }
        }
        if o.fold_axis_gutter {
            ink.union(&vega_fit::Bounds::new(
                pad_l,
                plot_top,
                plot_right + o.value_axis_right_overhang + m.right,
                plot_bottom,
            ));
        }
        let label_shift = vega_fit::baseline_shift("middle", y_font_size) + 0.5;
        PanelGeometry {
            content_start,
            svg_end,
            ink,
            axis_w_overhang: left_margin + m.right,
            left_mark_overhang: left_margin,
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
            widest_val_tick_label_width: y_label_ws.iter().fold(0.0_f64, |acc, &w| acc.max(w.min(y_limit))),
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
                .map(|(&frac, label)| (plot_top + y_pos(frac) + label_shift, label.clone()))
                .collect(),
            mirror_style: Some(GhostStyle {
                val_font_size: y_font_size,
                val_weight: y_label_weight.clone(),
                val_color: y_label_color.to_string(),
                val_family: y_family.to_string(),
            }),
            axis: PanelXAxis::Temporal(o.x_axis.clone()),
            top_axis_overhang: (-top_overhang.get()).max(0.0),
            legend: None,
        }
    });
    Ok((svg, geometry))
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
/// (`authored_domain_pair`), exactly as the legacy arm does. Refused by name:
/// a nonzero `scale.padding` and `scale.round` (both move the range), and a
/// datum outside the (niced) pair, which Vega draws unclipped past the plot
/// edge while `autosize: fit` grows the view around it.
fn authored_y_domain(ax_y: &AxisStyle, y_values: &[f64]) -> Result<Option<(f64, f64)>, String> {
    let Some((lo, hi)) = ax_y.authored_continuous_domain() else {
        return Ok(None);
    };
    let refuse = |term: &str| {
        Err(format!(
            "scatter: scale.{term} beside an authored y-axis domain is not supported on the \
             fit arm"
        ))
    };
    if ax_y.scale.get("padding").and_then(Value::as_f64).is_some_and(|p| p != 0.0) {
        return refuse("padding");
    }
    if ax_y.scale.get("round").and_then(Value::as_bool) == Some(true) {
        return refuse("round");
    }
    let (lo, hi) = crate::families::cartesian::authored_domain_pair(ax_y, lo, hi);
    let eps = (hi - lo).abs() * 1e-9;
    if let Some(&v) = y_values.iter().find(|&&v| v < lo.min(hi) - eps || v > lo.max(hi) + eps) {
        return Err(format!(
            "scatter: y value {v} lies outside the authored domain [{lo}, {hi}], which the \
             fit arm does not model"
        ));
    }
    Ok(Some((lo, hi)))
}

/// The `(lo, hi)` of a measure axis with no authored domain, on either arm.
/// It is Vega's linear scale as the scatter emitter compiles it
/// (`emitters/scatter.py:282` for x, `:354-358` for y): the column's extent
/// joined with 0 (Vega-Lite's default `zero: true`; `scale.continuous.zero:
/// false` skips the join), a baked `domain_min`/`domain_max` overwriting its
/// edge verbatim (`apply_domain_headroom_bounds`, `emitters/_cartesian.py:
/// 1265-1287`: the bounds are exact, never niced), and d3 nice at count 10
/// (the default `nice: true`) only when neither edge is baked — Vega-Lite
/// drops `nice` once `domainMin`/`domainMax` is set. That edge law is
/// `cartesian::resolve_measure_domain`, shared with line; headroom reaches an
/// edge only as a baked bound (`_domain.py:456-661`), so none is passed to
/// it. The emitter builds one scale whichever side the axis renders on, so
/// the right-axis fit arm and the left-axis legacy arm read the same law.
/// `data_lo`/`data_hi` are the extent of the rows Vega keeps.
fn unauthored_measure_domain(ax: &AxisStyle, data_lo: f64, data_hi: f64) -> (f64, f64) {
    let zero_false = ax.is_zero_false();
    let (lo, hi) =
        if zero_false { (data_lo, data_hi) } else { (data_lo.min(0.0), data_hi.max(0.0)) };
    crate::families::cartesian::resolve_measure_domain(
        lo,
        hi,
        ax.domain_min,
        ax.domain_max,
        ax.ticks.count,
        0.0,
        zero_false,
        !ax.has_baked_edge(),
    )
}

/// Refuses a scale term that reshapes the linear scale
/// `unauthored_measure_domain` draws: a scale type other than linear, a
/// nonzero `padding`, `round` and `x_reverse`, and `nice`. Vega-Lite's default
/// `nice` runs only with no baked edge, so beside one `nice: false` changes
/// nothing and draws, while an explicit `nice: true` makes Vega round the
/// baked bounds (`emit_resolved_scale_vl`, `vl_field_maps.py:99-115`) and
/// refuses; with no baked edge `nice` must be the default `true`. `arm` names
/// the arm in the message.
fn refuse_reshaped_scale(ax: &AxisStyle, field: &str, arm: &str) -> Result<(), String> {
    let refuse = |what: &str| {
        Err(format!("scatter: {what} on the {field:?} axis is not supported on the {arm} arm"))
    };
    if !ax
        .scale
        .get("continuous")
        .and_then(|c| c.get("type"))
        .is_none_or(|t| t.is_null() || t.as_str() == Some("linear"))
    {
        return refuse("a scale type other than linear");
    }
    let nice = ax.scale.get("nice");
    if ax.has_baked_edge() {
        if !matches!(nice, None | Some(Value::Null) | Some(Value::Bool(false))) {
            return refuse("an authored scale.nice other than false beside a baked domain edge");
        }
    } else if !matches!(nice, None | Some(Value::Null) | Some(Value::Bool(true))) {
        return refuse("an authored scale.nice other than true");
    }
    if ax.scale.get("padding").and_then(Value::as_f64).is_some_and(|p| p != 0.0) {
        return refuse("a nonzero scale.padding");
    }
    if ax.scale.get("round").and_then(Value::as_bool) == Some(true) {
        return refuse("scale.round");
    }
    if ax.scale.get("x_reverse").and_then(Value::as_bool) == Some(true) {
        return refuse("scale.x_reverse");
    }
    Ok(())
}

/// The `(lo, hi)` the legacy arm draws a measure axis over:
///
/// 1. An authored `scale.continuous.domain` wins (`authored_domain_pair`:
///    niced at count 10 only beside `scale.nice: true`).
/// 2. Otherwise `unauthored_measure_domain`.
///
/// Everything this arm does not draw refuses by name: a scale type other
/// than linear, `nice` other than `true`, a nonzero `padding`, `round`,
/// `x_reverse`, a `domain` that is not a numeric pair, a constant column with
/// no baked edge (Vega centers it on a one-tick axis), and any row outside
/// the drawn domain (Vega's `autosize: fit` pulls such a mark's ink into the
/// plot, and `clamp` would pin it to the edge).
///
/// `rows` are the rows Vega keeps: a row with a null or non-finite `x` or
/// `y` is dropped from the domain and the mark alike (Vega-Lite's default
/// `mark.invalid: "filter"` for a point mark: `mark.ts:351`,
/// `compile/invalid/normalizeInvalidDataMode.ts:9-10`; the filter itself is
/// `compile/data/filterinvalid.ts:36-55,85-99`, vega-lite v6.4.1), so a
/// dropped row neither stretches an axis nor trips the refusal above.
fn legacy_measure_domain(
    ax: &AxisStyle,
    rows: &[&serde_json::Map<String, Value>],
    field: &str,
) -> Result<(f64, f64), String> {
    refuse_reshaped_scale(ax, field, "legacy")?;
    let refuse = |what: &str| {
        Err(format!("scatter: {what} on the {field:?} axis is not supported on the legacy arm"))
    };
    let continuous = ax.scale.get("continuous");
    let authored = ax.authored_continuous_domain();
    if authored.is_none() && continuous.and_then(|c| c.get("domain")).is_some_and(|d| !d.is_null()) {
        return refuse("a scale.continuous.domain that is not a numeric pair");
    }
    let (lo, hi) = if let Some((lo, hi)) = authored {
        crate::families::cartesian::authored_domain_pair(ax, lo, hi)
    } else {
        let (data_lo, data_hi) = column_extent(rows.iter().copied(), field);
        if !data_lo.is_finite() {
            return Err(format!("scatter: column {field:?} has no numeric values"));
        }
        let (lo, hi) = unauthored_measure_domain(ax, data_lo, data_hi);
        if hi < lo || (hi == lo && ax.has_baked_edge()) {
            return Err(format!("scatter: the {field:?} axis domain is degenerate (max ≤ min)"));
        }
        (lo, hi)
    };
    let eps = (hi - lo).abs() * 1e-9;
    if let Some(v) = rows
        .iter()
        .filter_map(|row| row_num(row, field))
        .find(|&v| v < lo.min(hi) - eps || v > lo.max(hi) + eps)
    {
        return Err(format!(
            "scatter: {field:?} value {v} lies outside the drawn domain [{lo}, {hi}], which \
             this arm does not model"
        ));
    }
    Ok((lo, hi))
}

/// Refuses a point strictly inside an axis-free x edge but within its own
/// ink of it: the edge inset (`AXIS_FREE_EDGE_PAD`, or the ceil'd radius for a
/// point exactly on the edge) does not model a partial overhang, so the plot
/// rect would paint too wide for ink this arm cannot account for.
/// `distance_from_edge` maps an x value to its pixel distance from that edge.
fn refuse_partial_edge_overhang(
    rows: &[&serde_json::Map<String, Value>],
    x_field: &str,
    point_r: f64,
    edge: &str,
    distance_from_edge: impl Fn(f64) -> f64,
) -> Result<(), String> {
    let overhangs = rows.iter().any(|row| {
        row_num(row, x_field).is_some_and(|xv| {
            let d = distance_from_edge(xv);
            d > 0.0 && d < point_r - crate::common::AXIS_FREE_EDGE_PAD
        })
    });
    if overhangs {
        return Err(format!(
            "scatter: a point sits inside the x-axis {edge}, within its own ink of the edge — \
             this arm does not model a partial overhang inset"
        ));
    }
    Ok(())
}

fn render_legacy(
    payload: &Payload,
    fonts: &FontBook,
    r: &ScatterResolved,
) -> Result<String, String> {
    if r.style.axis_y.mirror_requested("scatter")? {
        return Err("scatter: axis_y.mirror is not modeled off the fit arm (a right y axis)".into());
    }
    // The size scale/legend law is ported on `render_fit` only — the
    // legacy arm's heuristic margins have no size-legend shape at all, and
    // `use_vega_fit`'s own gate already requires `legend.position ==
    // "right"` whenever a legend is shown, so this is also where a size
    // legend on a non-right orient lands.
    if r.size.is_some() {
        return Err("scatter: size channel is only supported on the fit shape (a right \
                     y axis, a right legend, an authored x label format, an untouched \
                     x scale)"
            .into());
    }
    // The chart's OWN effective background — see `ScatterResolved::background`.
    let frame = ChartFrame::of(payload, r.background.as_str());
    let (w, h) = (frame.w, frame.h);
    let (pad_l, pad_r, pad_t, pad_b) = frame.pad;

    let x_field = &r.x;
    let y_field = &r.y;
    // The rows Vega keeps (`legacy_measure_domain`): every domain (axes and
    // color), edge check and mark below reads these. Only the baseline-rule
    // gates further down scan `payload.rows`, per column: they port
    // `BaselineFeature`'s `numeric_column_values` (`features/baseline.py:155,
    // 238`; `core/utils.py:266`), which filters nothing across columns.
    let drawn = drawn_rows(&payload.rows, r, false, false, false);

    // Color domain: alphabetical (matches VL nominal default sort).
    let color_scale = match r.color.as_deref().filter(|_| quantitative_color(r)) {
        Some(cf) => {
            let (lo, hi) = color_extent(drawn.iter().copied(), cf);
            Some(ContinuousColor::blues(lo, hi))
        }
        None => None,
    };
    let color_domain: Vec<String> = if color_scale.is_some() {
        Vec::new()
    } else {
        r.color.as_deref().map_or_else(Vec::new, |cf| color_domain_of(drawn.iter().copied(), cf))
    };
    // The board's plan for this chart's color field, if it binds one
    // (#7167) — the point/legend fill defers to it over the local
    // alphabetical color_domain position.
    let category_scale: Option<&crate::common::CategoryColorScale> =
        r.color.as_deref().and_then(|cf| crate::common::category_scale_for(&r.category_colors, cf));

    let has_legend = !color_domain.is_empty() && r.legend.visible && r.legend.position.edge == "right";
    let legend_order = if has_legend {
        legend_entry_order(&r.legend, &color_domain, None)?
    } else {
        Vec::new()
    };
    let color_legend = if has_legend {
        Some(color_legend_layout(r, &color_domain, &legend_order, fonts, None)?)
    } else {
        None
    };

    // A shape channel's own legend, or (on the color field) glyphs merged into
    // the color legend; a rule layer's point keeps the two apart.
    let fires = RuleFires::of(r, &payload.rows);
    let rule_shape = fires.shape_layer(r);
    let merged_shape = shape_merges_into_color(r, rule_shape, false);
    let shape_domain = r
        .shape
        .as_deref()
        .map(|field| shape_scale_domain(drawn.iter().copied(), field, rule_shape))
        .unwrap_or_default();
    let shape_legend = match r.shape.as_deref().filter(|_| !merged_shape) {
        Some(field) => Some(crate::families::scatter_shape::ShapeLegend::new(fonts, payload, field.to_string(), shape_domain.clone())?),
        None => None,
    };

    // ── Y-axis domain and ticks ────────────────────────────────────────────
    let ax_y = &r.style.axis_y;
    let (y_domain_min, y_domain_max) = legacy_measure_domain(ax_y, &drawn, y_field)?;

    let y_ticks: Vec<f64> = if y_domain_min == y_domain_max {
        // A constant column: d3 maps every value to the range midpoint and its
        // one tick is the value itself.
        vec![y_domain_min]
    } else if !ax_y.tick_values.is_empty() {
        let y_span = (y_domain_max - y_domain_min).abs();
        let y_eps = y_span * 1e-9;
        ax_y.tick_values
            .iter()
            .copied()
            .filter(|&tv| tv <= y_domain_max.max(y_domain_min) + y_eps && tv >= y_domain_min.min(y_domain_max) - y_eps)
            .collect()
    } else {
        let count = ax_y.ticks.count.unwrap_or(6.0) as usize;
        d3rs::ticks::nice_tick_values(y_domain_min, y_domain_max, count, None)
    };

    // ── X-axis domain ─────────────────────────────────────────────────────
    let ax_x = &r.style.axis_x;
    let (x_domain_min, x_nice_hi) = legacy_measure_domain(ax_x, &drawn, x_field)?;
    if x_domain_min > x_nice_hi {
        return Err(format!(
            "scatter: the authored domain [{x_domain_min}, {x_nice_hi}] on the {x_field:?} axis is not ascending"
        ));
    }
    if x_domain_min == x_nice_hi {
        return Err(format!("scatter: a constant column on the {x_field:?} axis is not supported on the legacy arm"));
    }
    // An authored tick ladder past the domain's high extends the scale to
    // its last rung.
    let x_domain_max = ax_x.tick_values.last().map_or(x_nice_hi, |&last| last.max(x_nice_hi));
    // X ticks are generated below, after layout is known.

    // The fit arm's own symbol-ink term (`render_fit`'s point-ink union):
    // a point at the x floor sits at the plot's own pixel 0, so its own
    // radius, not the flat axis-free pad, decides the left inset.
    let point_r = symbol_radius(r.style.point_mark.size);
    let point_at_x_floor = drawn.iter().any(|row| row_num(row, x_field) == Some(x_domain_min));
    let point_at_x_ceiling = drawn.iter().any(|row| row_num(row, x_field) == Some(x_domain_max));

    // `x_gate_min`/`x_gate_max`: the raw, unconditioned scan the gate reads
    // (`column_extent`) is a DIFFERENT quantity from the zero-joined, niced
    // `x_domain_min`/`x_nice_hi` above, which feed the point/tick/gridline
    // geometry instead.
    // Computed here (not beside the paint site below, where the gate is
    // read again) because the fit arm's own rule-ink term
    // (`Bounds::expand(zx.width)`, the FULL stroke width) must land in the
    // top margin before `plot_top` is fixed.
    let (x_gate_min, x_gate_max) = column_extent(&payload.rows, x_field);
    let x_rule_fires = [(0.0, false), (1.0, true)].into_iter().any(|(datum, require_percent)| {
        crate::common::x_threshold_fires(ax_x, require_percent, datum, x_gate_min, x_gate_max)
    });

    // `axis_title_text`/`y_title_extra` are the SAME shared functions
    // `render_fit` reads its own title text and rotated-title reserve
    // through — never a second `slug_to_display` reading of the field
    // name, and never a second copy of Vega's `axisTitleLayout` term.
    let x_title_text = crate::common::axis_title_text(&r.x, r.x_label.as_deref(), &r.field_labels);
    let y_title_text = crate::common::axis_title_text(&r.y, r.y_label.as_deref(), &r.field_labels);
    let x_title_paint = crate::common::wrapped_axis_title(&r.id, "x_label", &x_title_text, &ax_x.title.font, w, fonts)?;
    let y_title_paint = crate::common::wrapped_axis_title(&r.id, "y_label", &y_title_text, &ax_y.title.font, h, fonts)?;
    let y_title_extra = crate::common::y_title_extra(&ax_y.title, &y_title_paint);

    // ── Bottom margin (x axis: ticks + labels + title) ─────────────────────
    let x_tick_len = ax_x.ticks.length.unwrap_or(6.0);
    // `vega-parser/src/parsers/axis.js:58-66`: `size = datum.ticks ? size : 0`
    // before `axisLabels` — a hidden tick contributes no length to the
    // label offset. `x_tick_offset` (axis.js:102) is the themed axis offset, a
    // separate term. Ported the same way `render_fit`'s own `x_tick_eff` does.
    let x_tick_eff = if ax_x.ticks.visible.unwrap_or(true) { x_tick_len } else { 0.0 };
    let x_tick_offset = ax_x.ticks.offset.unwrap_or(0.0);
    let x_label_pad = ax_x.labels.padding.unwrap_or(6.0);
    let x_label_font_size = ax_x.labels.font.size_or(11.0);
    let x_title_visible = ax_x.title.visible.unwrap_or(true);
    let x_title_pad = ax_x.title.padding.unwrap_or(10.0);
    let x_title_font_size = ax_x.title.font.size_or(11.0);
    let x_title_h = if x_title_visible {
        x_title_pad + x_title_font_size + crate::common::extra_title_lines(&x_title_paint, x_title_font_size)
    } else {
        0.0
    };
    let bottom_margin = x_tick_offset + x_tick_eff + x_label_pad + x_label_font_size + x_title_h;

    // ── Y-axis label width (measure formatted tick labels) ─────────────────
    let y_label_family = ax_y.labels.font.family_or("sans-serif");
    let y_label_font_size = ax_y.labels.font.size_or(11.0);
    let y_label_pad = ax_y.labels.padding.unwrap_or(6.0);
    // The tick length joins the label offset like the x axis's (`x_tick_eff`).
    let y_tick_eff = ax_y.tick_len();
    let y_ruler = ax_y.ruler.as_ref();
    let y_tick_label = ax_y.tick_label.as_ref();
    let y_labels_format = ax_y.labels.format.as_ref();
    // Precompute tick labels — used for both margin measurement and rendering.
    let y_tick_labels: Vec<String> = y_ticks
        .iter()
        .enumerate()
        .map(|(i, &tv)| {
            format_y_label(tv, y_ruler, y_tick_label, y_labels_format, i, y_ticks.len())
        })
        .collect::<Result<Vec<_>, _>>()?;
    let y_label_max_w = y_tick_labels
        .iter()
        .try_fold(0.0_f64, |acc, s| fonts.measure(s, y_label_family, y_label_font_size).map(|w| acc.max(w)))?;

    let plot_bottom = h - pad_b - bottom_margin;
    // Title-block law: paint and reservation come from ONE `TitleBlock`
    // (`docs/laws-shared.md`, "Title wrap law"), the `wrap_title_block`/
    // `emit_title_block_vega` pair every Vega arm shares. Production wraps
    // the title text on its own, whether or not a subtitle exists
    // (`title_overflow.py::apply_title_overflow_to_spec`, the `text` branch,
    // `:127-140`), and Vega's title block reserves `offset + ink_h` for the
    // wrapped lines (`common::wrap_title_block`). A title that fits yields
    // one unwrapped line. This arm never tiers `style.title_font`, so the
    // paint size and `wrap_title_block`'s measure size are the same
    // `title_measure_font_size`.
    let title = FrameTitle::new(
        r.title.as_deref(),
        r.subtitle.as_deref(),
        None,
        r.title_style.as_ref(),
        frame.avail_w(),
        fonts,
    )?;
    title.warn_truncated(&r.id);
    let title_h = title.ink().map_or(0.0, |ink| ink.offset + ink.ink_h);
    // The fit arm's own baseline-rule-ink term (`render_fit`'s
    // `Bounds::expand(zx.width)`): a fired x threshold rule's own stroke
    // overhangs the plot top by its FULL width, not half.
    let x_rule_width = if x_rule_fires {
        ax_x.grid.threshold.as_ref().and_then(|z| z.width).unwrap_or(2.0)
    } else {
        0.0
    };
    // The view's top ink is the union of the fired rule's stroke, every
    // point expanded by its radius (`render_fit`'s symbol ink: a point on the
    // y domain's max sits on the top edge and overhangs it by `point_r`) and
    // the topmost drawn y tick label's text box, which reaches `round(0.8·fs) −
    // baseline_shift("middle", fs)` above its tick (`vega_fit::text_ink`); a
    // tick on the plot's own top edge therefore overhangs it. Vega's margin
    // is the ceil of that union (`vega_fit.rs` `fit_plot`, `top = (-b.y1)
    // .ceil()`), measured against the plot height the previous pass left, for
    // exactly `AUTOSIZE_PADDING_PASSES` passes with the first at the full
    // canvas height: the pass count is Vega's, not a convergence test. A
    // label is measured iff the paint loop below draws it (the cover ladder
    // runs past the domain).
    let y_frac_from_top =
        |v: f64| LinearScale::new(y_domain_min, y_domain_max, 1.0, 0.0).position(v);
    let top_ink_at = |ph: f64| -> f64 {
        let label_ink = y_ticks.iter().filter_map(|&tv| {
            let y = ph * y_frac_from_top(tv);
            (-1.0..=ph + 1.0).contains(&y).then(|| {
                -vega_fit::text_ink(0.0, y, 0.0, y_label_font_size, "right", "middle").y1
            })
        });
        let point_ink = drawn.iter().map(|row| {
            let yv = row_num(row, y_field).expect("drawn rows carry both coordinates");
            let y = ph * y_frac_from_top(yv);
            -vega_fit::Bounds::new(0.0, y, 0.0, y).expand(point_r).y1
        });
        label_ink.chain(point_ink).fold(x_rule_width, f64::max).ceil()
    };
    let (mut top_ink, mut solve_ph) = (0.0, h);
    for _ in 0..vega_fit::AUTOSIZE_PADDING_PASSES {
        top_ink = top_ink_at(solve_ph);
        solve_ph = plot_bottom - (pad_t + title_h + top_ink);
    }
    let plot_top = pad_t + title_h + top_ink;
    let plot_h = plot_bottom - plot_top;

    // ── Legend panel width ─────────────────────────────────────────────────
    // The shared legend's measured box (`legend_from_widths`, the `legend_w`
    // `render_fit` unions into its ink), which Vega offsets from the plot's
    // right ink by its fixed legend gap.
    // A gradient's bar follows the plot's height, and a horizontal one its
    // width, which the legend's own margin narrows: settled in two passes.
    let gradient_title = match (&color_scale, r.color.as_deref()) {
        (Some(_), Some(cf)) if r.legend.visible => Some(legend::legend_title_text(cf, &r.legend, &r.field_labels)?),
        _ => None,
    };
    let gradient_id = legend_gradient_id(r);
    let gradient_block = |plot_w_at: f64| -> Result<Option<(f64, f64)>, String> {
        let (Some(scale), Some(title)) = (&color_scale, &gradient_title) else { return Ok(None) };
        GradientAt::measure(&r.legend, r.color_format.as_ref(), scale, (&gradient_id, title), fonts, (plot_w_at, plot_h))
            .map(Some)
    };
    let color_block = match (&color_legend, gradient_block(w - pad_l - pad_r)?) {
        (Some((_, lg)), _) => Some((lg.box_w, lg.box_h())),
        (None, Some(first)) => {
            let narrowed = w - pad_l - pad_r - (VEGA_LEGEND_OFFSET + first.0);
            gradient_block(narrowed)?
        }
        (None, None) => None,
    };
    // The legends stack down the right edge: a rule layer's shape legend first, else last.
    let shape_block = shape_legend.as_ref().map(|sl| (sl.w, sl.h));
    let (shape_y, color_y) = match (shape_block, color_block) {
        (Some(shape), Some(color)) => {
            let blocks = if rule_shape { [shape, color] } else { [color, shape] };
            let (offsets, _) = stack_legends(&blocks, false);
            if rule_shape { (offsets[0], offsets[1]) } else { (offsets[1], offsets[0]) }
        }
        _ => (0.0, 0.0),
    };
    let leg_panel_w = match (color_block.map(|(w, _)| w), shape_legend.as_ref().map(|sl| sl.w)) {
        (None, None) => 0.0,
        (a, b) => VEGA_LEGEND_OFFSET + a.unwrap_or(0.0).max(b.unwrap_or(0.0)),
    };

    // ── y-axis position ────────────────────────────────────────────────────
    let y_axis_right = ax_y.position.as_deref() == Some("right");

    // Own-side `labels.align: right` on a right axis bakes the label edge
    // as `max_w + VALUE_AXIS_BREATHING_ROOM`, not `max_w + labels.padding`
    // — the unaligned default. Computed once and read by every site that
    // shares this edge: the margin reservation below, the label anchor,
    // the y title, and the legend x.
    let y_label_edge_pad = if y_axis_right && ax_y.labels.align.as_deref() == Some("right") {
        crate::common::VALUE_AXIS_BREATHING_ROOM
    } else {
        y_label_pad
    };

    // ── Margins ────────────────────────────────────────────────────────────
    // The right edge's axis chrome: tick, labels and `y_title_extra` (the
    // fit arm's own rotated-title reserve, shared via `common::y_title_extra`;
    // 0.0 when the title is hidden) on a right value axis, nothing on a left
    // one. Vega's autosize takes integer extents (`vega_fit.rs` `fit_plot`,
    // `right = (b.x2 - pw).ceil()`), and the legend lands at `ceil(x2) + 18`
    // (`render_fit`'s `lx`), so the chrome rounds up before anything stacks
    // on it.
    let right_chrome = if y_axis_right {
        (y_label_max_w + y_tick_eff + y_label_edge_pad + y_title_extra).ceil()
    } else {
        0.0
    };
    let (left_margin, right_margin) = if y_axis_right {
        let rm = right_chrome + leg_panel_w;
        let lm = if point_at_x_floor {
            crate::common::AXIS_FREE_EDGE_PAD.max(point_r.ceil())
        } else {
            crate::common::AXIS_FREE_EDGE_PAD
        };
        (lm, rm)
    } else {
        // y axis on left: Vega lays the axis out with an integer extent over
        // the whole left ink (`vega_fit.rs` `left = (-b.x1).ceil()`): label
        // width, label padding and the rotated title's `axisTitleLayout`
        // reserve, which `y_title_extra` is 0.0 for when the title is hidden.
        let lm = (y_label_max_w + y_tick_eff + y_label_pad + y_title_extra).ceil();
        // No axis on the right: Vega's autosize margin is `ceil(b.x2 - pw)`
        // over the view's ink union (`vega_fit.rs` `right = (b.x2 - pw).ceil()`;
        // the fit arm unions every symbol expanded by its radius), which on
        // an axis-free edge is the measured `AXIS_FREE_EDGE_PAD`, or the
        // ceil'd radius when a point sits exactly on the x ceiling.
        let rm = if leg_panel_w > 0.0 {
            leg_panel_w
        } else if point_at_x_ceiling {
            crate::common::AXIS_FREE_EDGE_PAD.max(point_r.ceil())
        } else {
            crate::common::AXIS_FREE_EDGE_PAD
        };
        (lm, rm)
    };

    let plot_left = pad_l + left_margin;
    let plot_right = w - pad_r - right_margin;
    let plot_w = plot_right - plot_left;
    if plot_w <= 0.0 || plot_h <= 0.0 {
        return Err(format!("scatter: chart box {w}×{h} too small for content"));
    }

    // ── X ticks (authored count, same generator as render_fit) ─────────────
    // The domain is the niced extent; `d3_ticks`'s own last generated value
    // can land short of `x_nice_hi` whenever the authored tick count's step
    // is coarser than the count-10 nice step, and the scale bound is still
    // the domain's high, never the last tick.
    let x_tick_count = ax_x.ticks.count.unwrap_or(6.0) as usize;
    let x_ticks: Vec<f64> = if ax_x.tick_values.is_empty() {
        d3rs::ticks::d3_ticks(x_domain_min, x_nice_hi, x_tick_count)
    } else {
        ax_x.tick_values.clone()
    };

    // X scale (doesn't need title height).
    let x_scale = LinearScale::new(x_domain_min, x_domain_max, 0.0, plot_w);

    // `point_at_x_floor`/`point_at_x_ceiling` inset an edge for an EXACT
    // match only; a partial overhang refuses.
    if !point_at_x_floor {
        refuse_partial_edge_overhang(&drawn, x_field, point_r, "floor", |xv| {
            x_scale.position(xv)
        })?;
    }
    if !y_axis_right && leg_panel_w == 0.0 && !point_at_x_ceiling {
        refuse_partial_edge_overhang(&drawn, x_field, point_r, "ceiling", |xv| {
            plot_w - x_scale.position(xv)
        })?;
    }

    // ── Color lookup ───────────────────────────────────────────────────────
    let color_for = |series: &str| -> Result<String, String> {
        if color_domain.is_empty() {
            return Ok(r.style.single_series_fill.clone());
        }
        crate::common::series_color(series, &color_domain, &r.palette, category_scale, "#ccc")
    };

    // ── SVG emission ───────────────────────────────────────────────────────
    let mut svg = String::with_capacity(32768);
    frame.open(&mut svg);
    title.paint_at(&mut svg, pad_l, pad_t);


    let y_scale = LinearScale::new(y_domain_min, y_domain_max, plot_h, 0.0); // SVG y-flipped

    let _ = write!(svg, "<g transform=\"translate({plot_left:.2},{plot_top:.2})\">");

    // ── Gridlines ─────────────────────────────────────────────────────────
    // X gridlines (vertical)
    let x_grid = &ax_x.grid;
    if x_grid.visible.unwrap_or(false) {
        let gc = crate::common::required_ink(x_grid.color.as_deref(), "scatter", "axis_x.grid.color")?;
        let gw = x_grid.width.unwrap_or(1.0);
        let go = x_grid.opacity.unwrap_or(1.0);
        for &tv in &x_ticks {
            let x = x_scale.position(tv);
            if x < -0.5 || x > plot_w + 0.5 {
                continue;
            }
            let _ = write!(
                svg,
                "<line x1=\"{x:.2}\" y1=\"0\" x2=\"{x:.2}\" y2=\"{plot_h:.2}\" \
                 stroke=\"{gc}\" stroke-width=\"{gw}\" opacity=\"{go}\"/>", gc = xml_escape_attr(gc)
            );
        }
    }

    // Y gridlines (horizontal)
    let y_grid = &ax_y.grid;
    if y_grid.visible.unwrap_or(false) {
        let gc = crate::common::required_ink(y_grid.color.as_deref(), "scatter", "axis_y.grid.color")?;
        let gw = y_grid.width.unwrap_or(1.0);
        let go = y_grid.opacity.unwrap_or(1.0);
        for &tv in &y_ticks {
            let y = y_scale.position(tv);
            if y < -0.5 || y > plot_h + 0.5 {
                continue;
            }
            let _ = write!(
                svg,
                "<line x1=\"0\" y1=\"{y:.2}\" x2=\"{plot_w:.2}\" y2=\"{y:.2}\" \
                 stroke=\"{gc}\" stroke-width=\"{gw}\" opacity=\"{go}\"/>", gc = xml_escape_attr(gc)
            );
        }
    }

    // ── BaselineFeature's zero/unity/x-threshold rules ───────────────────
    // Production's `BaselineFeature` applies to every cartesian family
    // (bar/line/area/scatter — `applies_to`, `baseline.py`), not just
    // bar/line/area — a chart lands on THIS legacy arm only because its
    // margins/title take the heuristic path (an authored x scale
    // transform, a left y axis, a non-right legend or point value labels),
    // never because production skips the
    // rule feature for it. `y_zero_rule_fires`/`crate::common::
    // unity_rule_fires`/`crate::common::x_threshold_fires` are the SAME
    // gates `render_fit` calls, kept in shared functions so the two arms
    // cannot drift. Painted after the gridlines (matching the reference's
    // own paint order: its `role-mark` rule group follows the
    // `role-axis-grid` groups) — `_insert_rule` always routes a scatter's
    // rule to `spec.underlays` (#8343), painted beneath the points only —
    // in production's own `apply()` call order: y-zero, y-unity, x-zero,
    // x-unity.
    //
    //
    // The gates read `effective_measure_domain` and this arm's scales read
    // `legacy_measure_domain`; both honor an authored domain and a baked
    // edge, so a fired rule lands inside the plot.
    let (y_data_min, y_data_max) = column_extent(&payload.rows, y_field);
    // The wired marks' descriptions, as the fit arm builds them: a rule reads
    // `title: value` at its datum, a point its structured tooltip.
    let tooltip_labels = r.wide_measures.is_empty() && !r.style.tooltip_format.spec.is_empty();
    let x_tooltip_format = r.x_tooltip_row_format();
    let mut fired_rule_count = 0;
    let mut paint_rule = |svg: &mut String,
                          title: &str,
                          datum: f64,
                          point_at: (f64, f64),
                          (color, width): (&str, f64),
                          line: &dyn Fn(&str) -> String|
     -> Result<(), String> {
        let layer = fired_rule_count;
        let name = if rule_shape { format!("layer_{layer}_layer_0_marks") } else { format!("layer_{layer}_marks") };
        crate::contract::paint_item(
            svg, "rule", &name, crate::contract::ItemRole::RuleMark, crate::contract::Reads::Hidden,
            |aria| line(aria),
        );
        fired_rule_count += 1;
        if let Some(shape_field) = r.shape.as_deref().filter(|_| rule_shape) {
            let aria = rule_aria(tooltip_labels, &r.style.tooltip_format.spec, title, datum)?;
            paint_rule_point(svg, point_at, (shape_field, aria.as_ref(), layer), (color, width));
        }
        Ok(())
    };
    if let Some(z) = ax_y.grid.threshold.as_ref() {
        let zc = z.color.as_deref().unwrap_or("#8A8C8F");
        let zw = z.width.unwrap_or(2.0);
        for (datum, fired) in [
            (0.0, y_zero_rule_fires(ax_y, y_data_min, y_data_max)),
            (1.0, crate::common::unity_rule_fires(ax_y, y_data_min, y_data_max)),
        ] {
            if !fired {
                continue;
            }
            let gy = y_scale.position(datum);
            paint_rule(&mut svg, &y_title_text, datum, (0.0, gy), (zc, zw), &|label_attr| {
                format!(
                    "<line x1=\"0.00\" y1=\"{gy:.2}\" x2=\"{plot_w:.2}\" y2=\"{gy:.2}\" \
                     stroke=\"{zc}\" stroke-width=\"{zw}\"{label_attr}/>", zc = xml_escape_attr(zc)
                )
            })?;
        }
    }
    if let Some(zx) = ax_x.grid.threshold.as_ref() {
        let zc = zx.color.as_deref().unwrap_or("#8A8C8F");
        let zw = zx.width.unwrap_or(2.0);
        for (datum, require_percent) in [(0.0, false), (1.0, true)] {
            if crate::common::x_threshold_fires(ax_x, require_percent, datum, x_gate_min, x_gate_max)
            {
                let gx = x_scale.position(datum);
                paint_rule(&mut svg, &x_title_text, datum, (gx, 0.0), (zc, zw), &|label_attr| {
                    format!(
                        "<line x1=\"{gx:.2}\" y1=\"0.00\" x2=\"{gx:.2}\" y2=\"{plot_h:.2}\" \
                         stroke=\"{zc}\" stroke-width=\"{zw}\"{label_attr}/>", zc = xml_escape_attr(zc)
                    )
                })?;
            }
        }
    }
    // Points sit one layer past the last fired rule; with none the spec stays
    // un-layered and Vega names the group "marks".
    let points_layer_name =
        if fired_rule_count > 0 { format!("layer_{fired_rule_count}_marks") } else { "marks".to_string() };

    // ── Points ─────────────────────────────────────────────────────────────
    let opacity = r.style.point_mark.opacity.unwrap_or(1.0);
    let stroke_w = r.style.point_mark.stroke_width.unwrap_or(0.0);

    for row in &drawn {
        let xv = row_num(row, x_field).expect("drawn rows carry both coordinates");
        let yv = row_num(row, y_field).expect("drawn rows carry both coordinates");
        let cx = x_scale.position(xv);
        let cy = y_scale.position(yv);
        let series = r.color.as_deref().map(|cf| row_str(row, cf)).unwrap_or_default();
        let fill = match (&color_scale, r.color.as_deref()) {
            (Some(scale), Some(cf)) => {
                scale.fill(row_num(row, cf).expect("a drawn row carries its quantitative color reading"))
            }
            _ => color_for(&series)?,
        };
        let glyph = point_glyph(r, row, &shape_domain, point_r)?;
        let sw_attr = if stroke_w > 0.0 {
            format!(" stroke-width=\"{stroke_w}\"")
        } else {
            String::new()
        };
        let aria = if tooltip_labels {
            let header = r.color.as_deref().map(|cf| row_str(row, cf));
            let tt = crate::contract::ScatterTooltip {
                header: header.as_deref(),
                y_title: &y_title_text,
                y_value: crate::contract::ScatterValue::Quantitative(yv, &r.style.tooltip_format),
                x_title: &x_title_text,
                x_value: crate::contract::ScatterValue::Quantitative(xv, &x_tooltip_format),
            };
            Some(crate::contract::scatter_aria_label(&tt)?)
        } else {
            None
        };
        let label_attr = crate::contract::aria_attr(aria.as_deref(), crate::contract::ItemRole::Point);
        if aria.is_some() {
            crate::contract::mark_group_open(&mut svg, "symbol", &points_layer_name);
        }
        let _ = write!(
            svg,
            "<path transform=\"translate({cx:.4},{cy:.4})\" d=\"{glyph}\" \
             fill=\"{fill}\"{sw_attr} opacity=\"{opacity}\"{label_attr}/>", fill = xml_escape_attr(&fill)
        );
        if aria.is_some() {
            crate::contract::mark_group_close(&mut svg);
        }
    }

    let _ = write!(svg, "</g>"); // close plot group

    // ── X-axis ticks and labels (absolute coords) ──────────────────────────
    let x_label_family = ax_x.labels.font.family_or("sans-serif");
    let x_label_color = ax_x.labels.font.color.as_deref().unwrap_or("#626366");
    let x_label_weight = ax_x.labels.font.weight_str();
    let x_fmt = ax_x.labels.spec();
    let x_tick_visible = ax_x.ticks.visible.unwrap_or(true);
    let x_tick_color = crate::common::required_ink(ax_x.ticks.color.as_deref(), "scatter", "axis_x.ticks.color")?;
    let x_tick_w = ax_x.ticks.width.unwrap_or(1.5);
    // Axis line y position = bottom of plot + tick offset
    let x_axis_y = plot_bottom + x_tick_offset;

    // Precompute x-tick labels before SVG emission to propagate errors early.
    let x_tick_labels: Vec<String> = x_ticks
        .iter()
        .map(|&tv| match x_fmt {
            Some(spec) => crate::number_locale::d3_format(spec, tv)
                .map_err(|e| format!("scatter x-axis label format: {e}")),
            None => Ok(tv.to_string()),
        })
        .collect::<Result<Vec<_>, _>>()?;

    crate::contract::axis_group_open(&mut svg, crate::contract::AxisChannel::X, (plot_left, x_axis_y));
    for (i, (&tv, label)) in x_ticks.iter().zip(x_tick_labels.iter()).enumerate() {
        let abs_x = plot_left + x_scale.position(tv);
        if abs_x < plot_left - 1.0 || abs_x > plot_right + 1.0 {
            continue;
        }
        // Tick mark
        if x_tick_visible {
            let tick_y2 = x_axis_y + x_tick_len;
            let _ = write!(
                svg,
                "<line x1=\"{abs_x:.2}\" y1=\"{x_axis_y:.2}\" \
                 x2=\"{abs_x:.2}\" y2=\"{tick_y2:.2}\" \
                 stroke=\"{x_tick_color}\" stroke-width=\"{x_tick_w}\"/>", x_tick_color = xml_escape_attr(x_tick_color)
            );
        }
        let label_y = x_axis_y + x_tick_eff + x_label_pad + x_label_font_size * 0.75;
        // First tick left-anchored, last tick right-anchored, others centered.
        let anchor = if i == 0 {
            "start"
        } else if i + 1 == x_ticks.len() {
            "end"
        } else {
            "middle"
        };
        let _ = write!(
            svg,
            "<text x=\"{abs_x:.2}\" y=\"{label_y:.2}\" text-anchor=\"{anchor}\" \
             font-family=\"{}\" font-size=\"{x_label_font_size}\" \
             font-weight=\"{x_label_weight}\" fill=\"{x_label_color}\">{}</text>",
            vega_escape_attr(x_label_family),
            xml_escape(label), anchor = xml_escape_attr(anchor), x_label_weight = xml_escape_attr(&x_label_weight), x_label_color = xml_escape_attr(x_label_color)
        );
    }

    // X-axis title
    if x_title_visible {
        let title_text = &x_title_paint;
        let title_x = plot_left + plot_w / 2.0;
        let title_y = x_axis_y + x_tick_eff + x_label_pad + x_label_font_size + x_title_pad + x_title_font_size * 0.75;
        let title_family = ax_x.title.font.family_or("sans-serif");
        let title_color = ax_x.title.font.color.as_deref().unwrap_or("#8A8C8F");
        let title_weight = ax_x.title.font.weight_str();
        crate::contract::axis_title_group_open(&mut svg, Some(crate::contract::LabelKey::X));
        let _ = write!(
            svg,
            "<text x=\"{title_x:.2}\" y=\"{title_y:.2}\" text-anchor=\"middle\" \
             font-family=\"{}\" font-size=\"{x_title_font_size}\" \
             font-weight=\"{title_weight}\" fill=\"{title_color}\">",
            vega_escape_attr(title_family),
            title_weight = xml_escape_attr(&title_weight), title_color = xml_escape_attr(title_color)
        );
        crate::common::write_title_lines(&mut svg, &title_text.split('\n').collect::<Vec<_>>(), title_x, x_title_font_size);
        svg.push_str("</text>");
        crate::contract::mark_group_close(&mut svg);
    }
    crate::contract::axis_group_close(&mut svg);

    // ── Y-axis labels (absolute coords) ───────────────────────────────────
    let y_label_color = ax_y.labels.font.color.as_deref().unwrap_or("#626366");
    let y_label_weight = ax_y.labels.font.weight_str();

    // Label x depends on y-axis position.
    // Right-side: text-anchor="end" at plot_right + edge_pad + max_w (mirrors
    // line.rs approach) — `y_label_edge_pad`, the SAME pad the margin
    // reserved this edge with, never a second `labels.padding` reading.
    let y_label_anchor = "end";
    let y_label_x = if y_axis_right {
        plot_right + y_tick_eff + y_label_edge_pad + y_label_max_w
    } else {
        plot_left - y_tick_eff - y_label_pad
    };

    crate::contract::axis_group_open(&mut svg, crate::contract::AxisChannel::Y, (if y_axis_right { plot_right } else { plot_left }, plot_top));
    // One mark per rung, from the axis edge outward.
    if y_tick_eff > 0.0 {
        let (edge_x, outward) =
            if y_axis_right { (plot_right + 0.5, 1.0) } else { (plot_left + 0.5, -1.0) };
        let rows = y_ticks
            .iter()
            .map(|&tv| crate::common::tick_row(plot_top, y_scale.position(tv)))
            .filter(|&row| row >= plot_top - 1.0 && row <= plot_bottom + 1.0);
        crate::common::emit_value_ticks(&mut svg, &ax_y.ticks, rows, edge_x, outward, y_tick_eff)?;
    }

    for (&tv, label) in y_ticks.iter().zip(y_tick_labels.iter()) {
        let abs_y = plot_top + y_scale.position(tv);
        if abs_y < plot_top - 1.0 || abs_y > plot_bottom + 1.0 {
            continue;
        }
        let _ = write!(
            svg,
            "<text x=\"{y_label_x:.2}\" y=\"{abs_y:.2}\" dy=\"0.35em\" \
             text-anchor=\"{y_label_anchor}\" \
             font-family=\"{}\" font-size=\"{y_label_font_size}\" \
             font-weight=\"{y_label_weight}\" fill=\"{y_label_color}\">{}</text>",
            vega_escape_attr(y_label_family),
            xml_escape(label), y_label_anchor = xml_escape_attr(y_label_anchor), y_label_weight = xml_escape_attr(&y_label_weight), y_label_color = xml_escape_attr(y_label_color)
        );
    }

    // Y-axis title
    if y_axis_right {
        // The SAME shared function `render_fit` reads at scatter.rs:1845 —
        // `label_edge_x` (`plot_right + y_label_max_w + y_label_edge_pad`,
        // the label band's own outer edge) is the ONE input specific to
        // this arm; the padding, nudge, and visibility/empty-text gate all
        // live in `common::emit_axis_title_rotated` itself, never a second
        // reading of the law.
        let label_edge_x = plot_right + y_tick_eff + y_label_max_w + y_label_edge_pad;
        crate::common::emit_axis_title_rotated(
            &mut svg,
            &y_title_paint,
            &ax_y.title,
            label_edge_x,
            plot_top + plot_h / 2.0,
            1.0,
            Some(crate::contract::LabelKey::Y),
        );
    } else {
        // The label band's outer edge, as on the right axis: the margin's
        // ceil lands past the measured ink, so the title is placed from the
        // band, never from the plot edge minus the margin.
        let label_edge_x = plot_left - y_tick_eff - y_label_pad - y_label_max_w;
        crate::common::emit_axis_title_rotated(
            &mut svg,
            &y_title_paint,
            &ax_y.title,
            label_edge_x,
            plot_top + plot_h / 2.0,
            -1.0,
            Some(crate::contract::LabelKey::Y),
        );
    }
    crate::contract::axis_group_close(&mut svg);

    // ── Legend (right side) ────────────────────────────────────────────────
    let leg_x = plot_right + right_chrome + VEGA_LEGEND_OFFSET;
    if let Some((labels, lg)) = &color_legend {
        let fills = color_domain
            .iter()
            .map(|label| {
                crate::common::series_color(
                    label, &color_domain, &r.palette, category_scale, "#ccc",
                )
            })
            .collect::<Result<Vec<_>, _>>()?;
        let glyph_paths = color_legend_glyph_names(r, color_domain.len(), merged_shape)
            .iter()
            .map(|name| crate::families::scatter_shape::path(name, 5.0))
            .collect::<Result<Vec<_>, _>>()?;
        let entries: Vec<legend::LegendEntry> = legend_order
            .iter()
            .zip(labels)
            .map(|(&idx, label)| legend::LegendEntry {
                label,
                fill: &fills[idx],
                symbol: merged_shape.then(|| legend::LegendSymbol::FilledGlyph(&glyph_paths[idx])),
                series_key: None,
            })
            .collect();
        legend::emit_vega_legend(
            &mut svg,
            &entries,
            lg,
            &r.legend,
            legend::LegendSymbol::FilledCircle,
            leg_x,
            plot_top + color_y,
            false,
        );
    }
    if let (Some(scale), Some(title)) = (&color_scale, &gradient_title) {
        GradientAt::paint(
            &r.legend,
            r.color_format.as_ref(),
            scale,
            (&gradient_id, title),
            fonts,
            (plot_w, plot_h),
            (&mut svg, leg_x, plot_top + color_y),
        )?;
    }
    if let Some(sl) = &shape_legend {
        sl.paint(&mut svg, leg_x, plot_top + shape_y, shape_legend_fill(r, &fires), r.style.point_mark.opacity.unwrap_or(1.0));
    }

    svg.push_str("</svg>");
    Ok(svg)
}


#[cfg(test)]
mod tests {
    use super::render;
    use crate::text::FontBook;
    use crate::Payload;

    const TEST_FONT: &str = "'Inter Variable', Inter, sans-serif";

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
                "title": {"font": {"family": TEST_FONT, "size": 12.0, "weight": "600", "color": "#313233"}},
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

    /// Off the right edge a color legend anchors through `legend_origin` on
    /// the fit arm only: the legacy arm draws the right strip alone, so a
    /// legend on any other edge would vanish there while production draws
    /// it, and each edge refuses by name.
    #[test]
    fn a_legend_off_the_right_edge_refuses_on_the_legacy_arm() {
        for edge in ["left", "bottom", "top"] {
            let mut p = make_scatter_payload(true);
            p["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
            p["resolved"]["legend"]["position"]["edge"] = serde_json::json!(edge);
            let payload: Payload = serde_json::from_value(p).unwrap();
            let err = render(&payload, &test_fonts()).unwrap_err();
            assert!(
                err.contains(&format!("scatter: legend.position=\"{edge}\"")) && err.contains("legacy margin arm"),
                "{edge}: {err}"
            );
        }
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
            attr(&svg[svg[..end].rfind("<text").unwrap()..end], "y") - crate::vega_fit::baseline_shift("top", 12.0)
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

    /// `render_legacy`'s own `r.background.as_str()` read (see
    /// `ScatterResolved::background`) feeds the canvas rect independently
    /// of `render_fit`'s own site.
    #[test]
    fn legacy_arm_canvas_rect_reads_the_chart_own_background_not_the_board_background() {
        let mut json = make_scatter_payload(false);
        json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
        json["resolved"]["background"] = serde_json::json!("#d9a45b");
        json["background"] = serde_json::json!("#ffffff");
        let svg = render_json(json);
        assert!(svg.contains("<rect width=\"600\" height=\"432\" fill=\"#d9a45b\"/>"), "{svg}");
    }

    /// Same law on the LEGACY arm (a left y axis routes here instead).
    #[test]
    fn legacy_arm_no_color_channel_paints_single_series_fill() {
        let mut p = make_scatter_payload(false);
        p["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
        let svg = render_json(p);
        assert_eq!(svg.matches("fill=\"#23467f\"").count(), 3, "all points: {svg}");
        assert!(!svg.contains("fill=\"#3164a3\""), "never palette[0]: {svg}");
    }

    fn legacy_plot_top(svg: &str) -> f64 {
        let i = svg.find("<g transform=\"translate(").expect("plot group");
        svg[i..].split(',').nth(1).unwrap().split(')').next().unwrap().trim().parse().unwrap()
    }

    fn legacy_plot_left(svg: &str) -> f64 {
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
    fn legacy_arm_draws_and_reserves_the_subtitle() {
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

        let delta = legacy_plot_top(&with_sub) - legacy_plot_top(&without);
        let want = 13.0 + crate::constants::layout::VEGA_SUBTITLE_PADDING;
        assert_eq!(delta, want, "the subtitle band is reserved: plot moved {delta}, expected {want}");
    }

    /// A `wrap-two` subtitle (the exact `p1_scatter_y_only` text) wraps to
    /// two lines and reserves `TitleInk::with_subtitle_lines`' own `ink_h`,
    /// not a single line's.
    #[test]
    fn legacy_arm_wraps_and_reserves_a_two_line_subtitle() {
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
        let delta = legacy_plot_top(&wrapped) - legacy_plot_top(&one_line);
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
    fn legacy_arm_wraps_a_long_title_and_paints_the_subtitle_under_both_lines() {
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
    fn legacy_arm_wraps_and_reserves_a_long_title_with_no_subtitle() {
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
            legacy_plot_top(&wrapped) - legacy_plot_top(&one_line),
            advance,
            "the reserved band counts the second line: {wrapped}"
        );
    }

    /// Hidden ticks contribute no length to the axis label offset
    /// (`vega-parser/src/parsers/axis.js:58-66`), the same law `render_fit`'s
    /// own `x_tick_eff` already ports.
    #[test]
    fn legacy_arm_hidden_x_ticks_shrink_the_bottom_margin_by_the_tick_length() {
        let build = |ticks_visible: bool| {
            let mut p = make_scatter_payload(false);
            p["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
            p["resolved"]["style"]["axis_x"]["ticks"]["visible"] = serde_json::json!(ticks_visible);
            p
        };
        let (_, ph_hidden) = legacy_plot_size(&render_json(build(false)));
        let (_, ph_visible) = legacy_plot_size(&render_json(build(true)));
        assert_eq!(
            ph_hidden - ph_visible,
            6.0,
            "visible ticks reserve exactly ticks.length (6.0) more bottom margin, \
             shrinking the plot by that much: {ph_hidden} vs {ph_visible}"
        );
    }

    /// Vega's autosize reserves `offset + tick + label padding + label text
    /// box` below the plot and starts the x axis group `offset` past it, the
    /// themed `axis_x.ticks.offset` (probe:
    /// `test_the_bottom_chrome_is_the_axis_offset_plus_tick_pad_label_and_title`).
    #[test]
    fn legacy_arm_bottom_margin_and_axis_follow_the_themed_x_offset() {
        for offset in [0.0, 1.0, 3.0] {
            let mut p = make_scatter_payload(false);
            p["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
            let ticks = &mut p["resolved"]["style"]["axis_x"]["ticks"];
            ticks["visible"] = serde_json::json!(true);
            ticks["offset"] = serde_json::json!(offset);
            let svg = render_json(p);
            let (_, plot_h) = legacy_plot_size(&svg);
            let plot_bottom = legacy_plot_top(&svg) + plot_h;
            // offset + tick 6 + label padding 6 + 11px label box.
            assert_eq!(plot_bottom + offset + 23.0, 432.0 - 16.0, "offset {offset}: {svg}");
            let first_tick = svg
                .split("<line ")
                .skip(1)
                .find(|t| attr(t, "y2") - attr(t, "y1") == 6.0 && attr(t, "x1") == attr(t, "x2"))
                .expect("an x tick mark");
            assert_eq!(attr(first_tick, "y1"), plot_bottom + offset, "offset {offset}: {svg}");
        }
    }

    /// Vega draws a tick per rung on a left value axis, ending at the axis
    /// edge, and anchors its labels at `-(tickSize + labelPadding)`
    /// (`utils.py:853-860`): the plot gives up the tick length while the
    /// labels keep their absolute x.
    #[test]
    fn legacy_arm_left_value_axis_paints_and_reserves_the_visible_tick() {
        let render_with = |visible: bool, length: f64| {
            let mut p = make_scatter_payload(false);
            let axis_y = &mut p["resolved"]["style"]["axis_y"];
            axis_y["position"] = serde_json::json!("left");
            axis_y["ticks"] =
                serde_json::json!({"color": "#D0D3D9", "visible": visible, "count": 6.0, "length": length});
            render_json(p)
        };
        // Horizontal `<line>`s exactly `len` long: a gridline spans the plot.
        let marks = |svg: &str, len: f64| -> Vec<(f64, f64)> {
            svg.split("<line ")
                .skip(1)
                .filter(|t| {
                    (attr(t, "x2") - attr(t, "x1") - len).abs() < 1e-6
                        && attr(t, "y1") == attr(t, "y2")
                })
                .map(|t| (attr(t, "x2"), attr(t, "y1")))
                .collect()
        };
        let label_xs = |svg: &str| -> Vec<f64> {
            svg.split("<text ")
                .skip(1)
                .filter(|t| t.contains("dy=\"0.35em\"") && t.contains("text-anchor=\"end\""))
                .map(|t| attr(t, "x"))
                .collect()
        };
        let off = render_with(false, 6.0);
        assert!(!label_xs(&off).is_empty(), "the arm paints value labels: {off}");
        for length in [6.0, 14.0] {
            assert!(marks(&off, length).is_empty());
            let on = render_with(true, length);
            let ticks = marks(&on, length);
            assert_eq!(ticks.len(), 3, "one mark per rung ({length}): {on}");
            assert_eq!(legacy_plot_left(&on) - legacy_plot_left(&off), length, "{length}");
            for (x2, _) in &ticks {
                let edge = legacy_plot_left(&on) + 0.5;
                assert_eq!(*x2, edge, "the mark ends at the axis edge ({length})");
            }
            assert_eq!(label_xs(&on), label_xs(&off), "labels keep their absolute x ({length})");
        }
    }

    /// The legacy arm's right axis wears the same tick: the plot gives up the
    /// tick length and each mark starts at the axis edge.
    #[test]
    fn legacy_arm_right_value_axis_paints_and_reserves_the_visible_tick() {
        let render_with = |visible: bool| {
            let mut p = make_scatter_payload(false);
            // An x axis with no authored label format keeps the legacy margins.
            p["resolved"]["style"]["axis_x"]["labels"]["format"] = serde_json::Value::Null;
            p["resolved"]["style"]["axis_y"]["ticks"] =
                serde_json::json!({"color": "#D0D3D9", "visible": visible, "count": 6.0, "length": 9.0});
            render_json(p)
        };
        let marks = |svg: &str| -> Vec<f64> {
            svg.split("<line ")
                .skip(1)
                .filter(|t| {
                    (attr(t, "x2") - attr(t, "x1") - 9.0).abs() < 1e-6
                        && attr(t, "y1") == attr(t, "y2")
                })
                .map(|t| attr(t, "x1"))
                .collect()
        };
        let (off, on) = (render_with(false), render_with(true));
        assert!(marks(&off).is_empty());
        let starts = marks(&on);
        assert_eq!(starts.len(), 3, "one mark per rung: {on}");
        let (off_w, _) = legacy_plot_size(&off);
        let (on_w, _) = legacy_plot_size(&on);
        assert_eq!(off_w - on_w, 9.0, "the plot gives up the tick length");
        for x1 in starts {
            assert_eq!(x1, legacy_plot_left(&on) + on_w + 0.5, "the mark starts at the axis edge");
        }
    }

    /// The axis-free left edge reads `common::AXIS_FREE_EDGE_PAD`
    /// (`common.rs`'s own measured citation), the same shared constant
    /// bar.rs/line.rs use — never a second, family-local reading of it.
    #[test]
    fn legacy_arm_right_axis_left_pad_matches_the_shared_axis_free_edge_pad() {
        let mut p = make_scatter_payload(false);
        p["resolved"]["style"]["axis_x"]["labels"]["format"] = serde_json::Value::Null;
        let svg = render_json(p);
        assert_eq!(
            legacy_plot_left(&svg),
            16.0 + crate::common::AXIS_FREE_EDGE_PAD,
            "pad_l + the shared axis-free-edge inset, not the local 4.0 constant: {svg}"
        );
    }

    /// `x_threshold_payload` routed to `render_legacy` by an unbaked y
    /// ladder (`y_measure` needs one) instead of a left y axis, so
    /// `axis_y.position` stays `"right"` and exercises the axis-free-edge
    /// branch the tests below need, with the x axis left unauthored.
    fn x_threshold_payload_forcing_legacy(mutate: impl FnOnce(&mut serde_json::Value)) -> serde_json::Value {
        let mut json = x_threshold_payload(|_| {});
        json["resolved"]["style"]["axis_y"]["tick_values"] = serde_json::json!([]);
        mutate(&mut json);
        json
    }

    /// The fit arm's own symbol-ink term (`scatter.rs`'s `render_fit`,
    /// point ink unioned into the fit): a point sitting exactly at
    /// `x_domain_min` widens the axis-free left edge to its own ceil'd
    /// radius, past the flat `AXIS_FREE_EDGE_PAD`.
    #[test]
    fn legacy_arm_right_axis_widens_the_left_pad_for_a_point_at_the_x_domain_floor() {
        let with_point_at_floor = x_threshold_payload_forcing_legacy(|json| {
            json["resolved"]["style"]["axis_x"]["grid"]["threshold"] = serde_json::Value::Null;
        });
        let without_point_at_floor = x_threshold_payload_forcing_legacy(|json| {
            json["resolved"]["style"]["axis_x"]["grid"]["threshold"] = serde_json::Value::Null;
            json["resolved"]["style"]["axis_x"]["domain_min"] = serde_json::json!(-10.0);
        });
        let with_svg = render_json(with_point_at_floor);
        let without_svg = render_json(without_point_at_floor);
        assert_eq!(
            legacy_plot_left(&with_svg),
            16.0 + crate::common::symbol_radius(60.0).ceil(),
            "a point at the x domain floor widens the pad to its own ceil'd radius: {with_svg}"
        );
        assert_eq!(
            legacy_plot_left(&without_svg),
            16.0 + crate::common::AXIS_FREE_EDGE_PAD,
            "no point at the floor keeps the plain axis-free pad: {without_svg}"
        );
    }

    /// A point just INSIDE the x floor (not exactly on it) still overhangs
    /// the left edge by its own ink, the same shape `point_at_x_floor`'s
    /// exact-equality gate is blind to. Refuse rather than silently paint
    /// the plot rect too wide for ink this arm does not model.
    #[test]
    fn legacy_arm_refuses_a_point_just_inside_the_x_domain_floor() {
        let just_inside = x_threshold_payload_forcing_legacy(|json| {
            json["resolved"]["style"]["axis_x"]["grid"]["threshold"] = serde_json::Value::Null;
            json["rows"][0]["spend"] = serde_json::json!(0.001);
        });
        let payload: Payload = serde_json::from_value(just_inside).unwrap();
        assert!(
            render(&payload, &test_fonts()).is_err(),
            "a point sitting inside the x floor, within its own ink of the \
             edge, is unmodeled here — refuse rather than mis-render"
        );
    }

    /// The fit arm's own baseline-rule-ink term (`render_fit`'s
    /// `expand(zx.width)` — the FULL stroke width, not half): a fired x
    /// threshold rule widens the top margin by its own stroke width. The
    /// authored width (3.0) deliberately differs from `x_threshold_fires`'s
    /// own default (2.0), so a hard-coded literal in place of the field
    /// read cannot pass this by coincidence.
    #[test]
    fn legacy_arm_x_threshold_rule_widens_the_top_margin_by_its_own_stroke_width() {
        let with_rules = x_threshold_payload_forcing_legacy(|json| {
            json["resolved"]["style"]["axis_x"]["domain_min"] = serde_json::json!(-10.0);
            json["resolved"]["style"]["axis_x"]["grid"]["threshold"]["width"] = serde_json::json!(3.0);
        });
        let without_rules = x_threshold_payload_forcing_legacy(|json| {
            json["resolved"]["style"]["axis_x"]["domain_min"] = serde_json::json!(-10.0);
            json["resolved"]["style"]["axis_x"]["grid"]["threshold"] = serde_json::Value::Null;
        });
        let with_svg = render_json(with_rules);
        let without_svg = render_json(without_rules);
        let delta = legacy_plot_top(&with_svg) - legacy_plot_top(&without_svg);
        assert_eq!(
            delta,
            3.0,
            "the fired rule's own authored stroke width (3.0) widens the top margin: {with_svg}"
        );
    }

    /// `common::y_title_extra`, the fit arm's own rotated-title reserve
    /// (Vega's real `axisTitleLayout`), is what this arm's right-axis
    /// branch reserves past the labels when the y title is visible.
    #[test]
    fn legacy_arm_right_axis_reserves_the_rotated_y_title_band() {
        let build = |title_visible: bool| {
            let mut p = make_scatter_payload(false);
            p["resolved"]["style"]["axis_x"]["scale"] =
                serde_json::json!({"continuous": {"domain": [0.0, 100.0]}});
            p["resolved"]["style"]["axis_y"]["title"]["visible"] = serde_json::json!(title_visible);
            p
        };
        let visible = render_json(build(true));
        let hidden = render_json(build(false));
        let (pw_hidden, _) = legacy_plot_size(&hidden);
        let (pw_visible, _) = legacy_plot_size(&visible);
        // Title font size 11, padding 10 (axis_json's own theme defaults):
        // 10 + round(0.21*11) + round(0.8*11) = 10 + 2 + 9 = 21.
        assert_eq!(
            pw_hidden - pw_visible,
            21.0,
            "the visible title's own reserve shrinks the plot by exactly \
             y_title_extra: hidden={pw_hidden} visible={pw_visible}"
        );
    }

    /// The left-axis branch composes its margin as Vega lays the axis out:
    /// `ceil(label_w + label_pad + y_title_extra)`, the ceil taken over the
    /// whole left ink (`vega_fit.rs` `left = (-b.x1).ceil()`), with the
    /// rotated title's reserve (`axisTitleLayout`: padding + round(0.21 fs) +
    /// round(0.8 fs)) present only while the title is visible. Probed
    /// (`negative-unbaked-axes`, reference origin 44 hidden / 65 visible).
    #[test]
    fn legacy_arm_left_axis_margin_is_the_ceil_of_label_pad_and_title_reserve() {
        let rows = [(10.0, 100.0), (20.0, 180.0), (30.0, 220.0), (40.0, 300.0)];
        let fonts = test_fonts();
        let label_w = ["0", "50", "100", "150", "200", "250", "300"]
            .iter()
            .map(|s| fonts.measure(s, TEST_FONT, 11.0).unwrap())
            .fold(0.0_f64, f64::max);
        for (title_visible, title_reserve) in [(false, 0.0), (true, 21.0)] {
            let mut json = legacy_unbaked_payload(&rows);
            json["resolved"]["style"]["axis_y"]["title"]["visible"] =
                serde_json::json!(title_visible);
            let svg = render_json(json);
            let expected = 16.0 + (label_w + 6.0 + title_reserve).ceil();
            assert_eq!(
                legacy_plot_left(&svg),
                expected,
                "title_visible={title_visible}, label_w={label_w}: {svg}"
            );
        }
    }

    /// The left rotated title sits where the shared emitter puts it: past
    /// the label band's outer edge by `padding + round(0.21 fs)`, whatever
    /// the margin's ceil adds beyond the measured ink.
    #[test]
    fn legacy_arm_left_axis_title_is_placed_from_the_label_bands_outer_edge() {
        let rows = [(10.0, 100.0), (20.0, 180.0), (30.0, 220.0), (40.0, 300.0)];
        let mut json = legacy_unbaked_payload(&rows);
        json["resolved"]["style"]["axis_y"]["title"]["visible"] = serde_json::json!(true);
        let svg = render_json(json);
        let fonts = test_fonts();
        let label_w = ["0", "50", "100", "150", "200", "250", "300"]
            .iter()
            .map(|s| fonts.measure(s, TEST_FONT, 11.0).unwrap())
            .fold(0.0_f64, f64::max);
        let title = svg
            .split("<text ")
            .skip(1)
            .find(|t| t.contains("rotate(-90 "))
            .unwrap_or_else(|| panic!("a rotated y title is painted: {svg}"));
        let expected = legacy_plot_left(&svg) - 6.0 - label_w - (10.0 + 2.0);
        assert!((attr(title, "x") - expected).abs() < 0.01, "{title} vs {expected}");
    }

    /// A y tick label overhangs the plot top by `round(0.8·fs) −
    /// baseline_shift("middle", fs)` (the text bound's box top, 9 − 3 = 6 at
    /// 11px) less the tick's own distance from the top; the autosize margin
    /// is the ceil of that overhang, solved against the plot height it
    /// shrinks (`vega_fit.rs` `top = (-b.y1).ceil()`). Probed
    /// (`vl_probe.layout_facts`, `nice: false`, fit/padding, height 300, a
    /// tick every 50): domain [0, 500] with a tick at the top gives top
    /// margin 6, [0, 504] gives 4, [0, 510] gives 1, [0, 520] gives 0. Here
    /// the ladder is this arm's own (0, 200, 400), so [0, 404] puts its top
    /// tick 4 domain units below the edge.
    #[test]
    fn legacy_arm_top_margin_is_the_y_label_overhang_solved_against_the_plot_height() {
        let rows = [(10.0, 100.0), (20.0, 180.0), (30.0, 220.0), (40.0, 300.0)];
        for (top, expected_overhang) in [(500.0, Some(6.0)), (520.0, None)] {
            let mut json = legacy_unbaked_payload(&rows);
            json["resolved"]["style"]["axis_y"]["scale"] =
                serde_json::json!({"continuous": {"domain": [0.0, top]}});
            let svg = render_json(json);
            assert_eq!(
                legacy_plot_top(&svg),
                16.0 + expected_overhang.unwrap_or(0.0),
                "domain [0, {top}]: {svg}"
            );
        }
        let mut json = legacy_unbaked_payload(&rows);
        json["resolved"]["style"]["axis_y"]["scale"] =
            serde_json::json!({"continuous": {"domain": [0.0, 404.0]}});
        let svg = render_json(json);
        let (_, plot_h) = legacy_plot_size(&svg);
        let top_margin = legacy_plot_top(&svg) - 16.0;
        assert_eq!(top_margin, (6.0 - 4.0 / 404.0 * plot_h).ceil(), "plot_h {plot_h}: {svg}");
        assert_eq!(top_margin, 3.0, "{svg}");
    }

    /// A right-axis legacy payload: an unbaked y ladder routes to
    /// `render_legacy` (`y_measure` needs a baked one) with the axis kept on
    /// the right.
    fn legacy_right_axis_payload() -> serde_json::Value {
        let mut json = make_scatter_payload(false);
        json["resolved"]["style"]["axis_y"]["tick_values"] = serde_json::json!([]);
        json["resolved"]["style"]["axis_y"]["labels"]["align"] = serde_json::Value::Null;
        json
    }

    /// Vega's autosize takes integer extents (`vega_fit::fit_plot`, `right =
    /// (b.x2 - pw).ceil()`), so the right axis's label chrome rounds up to a
    /// whole pixel and the plot ends on one: probed in
    /// `tests/test_vl_probe.py::test_a_right_axis_margin_is_an_integer_extent`.
    #[test]
    fn legacy_arm_right_axis_margin_is_the_ceil_of_its_chrome() {
        let fonts = test_fonts();
        let label_w = ["150", "200", "250"]
            .iter()
            .map(|s| fonts.measure(s, TEST_FONT, 11.0).unwrap())
            .fold(0.0_f64, f64::max);
        assert!(label_w.fract() != 0.0, "a whole-pixel label would not pin the ceil: {label_w}");
        let svg = render_json(legacy_right_axis_payload());
        let (plot_w, _) = legacy_plot_size(&svg);
        let chrome = label_w + 6.0;
        assert_eq!(legacy_plot_left(&svg) + plot_w, 600.0 - 16.0 - chrome.ceil(), "{svg}");
    }

    /// Vega's view bounds union every point expanded by its radius
    /// (`boundStroke` of a symbol reaches `sqrt(size) / 2` past its scale
    /// position), so the highest point sitting on the top edge reserves
    /// `ceil(radius)`: probed in `tests/test_vl_probe.py::
    /// test_a_point_on_the_top_edge_reserves_the_ceil_of_its_radius`.
    #[test]
    fn legacy_arm_top_margin_reserves_the_ceil_of_a_point_on_the_top_edge() {
        let build = |size: f64, top_row: f64| {
            let mut json = legacy_unbaked_payload(&[(10.0, 100.0), (20.0, 200.0), (30.0, top_row)]);
            json["resolved"]["style"]["axis_y"]["tick_values"] = serde_json::json!([100.0, 200.0]);
            json["resolved"]["style"]["axis_y"]["scale"] =
                serde_json::json!({"continuous": {"domain": [0.0, 300.0]}});
            json["resolved"]["style"]["point_mark"]["size"] = serde_json::json!(size);
            legacy_plot_top(&render_json(json))
        };
        assert_eq!(build(60.0, 300.0), 16.0 + 4.0, "ceil(sqrt(60) / 2 = 3.87)");
        assert_eq!(build(200.0, 300.0), 16.0 + 8.0, "ceil(sqrt(200) / 2 = 7.07)");
        assert_eq!(build(60.0, 250.0), 16.0, "a point below the edge reserves nothing");
    }

    /// A legacy scatter with a color legend of the series `S`, `A`, `B`
    /// (`fixtures/scatter-specifics/null-coordinate-row-extent-legacy-series`).
    fn legacy_series_payload() -> serde_json::Value {
        let mut json = legacy_unbaked_payload(&[(10.0, 100.0), (20.0, 180.0), (30.0, 220.0)]);
        json["rows"] = serde_json::json!([
            {"spend": 10.0, "pipeline": 100.0, "s": "A"},
            {"spend": 20.0, "pipeline": 180.0, "s": "B"},
            {"spend": 30.0, "pipeline": 220.0, "s": "A"},
        ]);
        json["resolved"]["color"] = serde_json::json!("s");
        json["resolved"]["legend"]["visible"] = serde_json::json!(true);
        json
    }

    /// The legend panel is the legend's own measured box (28 wide for the
    /// entries `A`, `B` and the title `S`, the reference's width) and Vega
    /// offsets it 18px from the plot (`VEGA_LEGEND_OFFSET`), with nothing
    /// floored: the reference's plot ends at `600 - 16 - 46`.
    #[test]
    fn legacy_arm_legend_panel_is_the_measured_box_offset_by_vega_s_legend_gap() {
        let svg = render_json(legacy_series_payload());
        let (plot_w, _) = legacy_plot_size(&svg);
        let plot_right = legacy_plot_left(&svg) + plot_w;
        assert_eq!(plot_right, 600.0 - 16.0 - (18.0 + 28.0), "{svg}");
        assert_eq!(legacy_legend_title_x(&svg), plot_right + 18.0, "{svg}");
    }

    /// A right-axis legend stacks on the whole axis chrome, the visible
    /// rotated y title's `y_title_extra` included (`render_fit`'s `lx` is
    /// `ceil(plot_w + label gutter + y_title_extra) + 18`): the margin is
    /// `ceil(label_w + tick + edge pad + y_title_extra) + VEGA_LEGEND_OFFSET +
    /// box_w`. Probed in `tests/test_vl_probe.py::
    /// test_a_right_legend_is_its_measured_box_offset_18px_from_the_axis_chrome`.
    #[test]
    fn legacy_arm_right_axis_legend_stacks_on_the_visible_y_title_band() {
        let fonts = test_fonts();
        let mut json = legacy_right_axis_payload();
        json["resolved"]["color"] = serde_json::json!("segment");
        json["resolved"]["legend"]["visible"] = serde_json::json!(true);
        json["rows"] = make_scatter_payload(true)["rows"].clone();
        json["resolved"]["style"]["axis_y"]["title"]["visible"] = serde_json::json!(true);
        let title: crate::common::AxisTitle =
            serde_json::from_value(json["resolved"]["style"]["axis_y"]["title"].clone()).unwrap();
        let title_extra = crate::common::y_title_extra(
            &title,
            &crate::common::axis_title_text("pipeline", None, &crate::common::FieldLabels::new()),
        );
        assert!(title_extra > 0.0, "the visible title must reserve a band: {title_extra}");
        let label_w = ["150", "200", "250"]
            .iter()
            .map(|s| fonts.measure(s, TEST_FONT, 11.0).unwrap())
            .fold(0.0_f64, f64::max);
        let (_, _, (box_w, _)) = edge_legend("right", "vertical");
        let chrome = (label_w + 6.0 + title_extra).ceil();

        let svg = render_json(json);
        let (plot_w, _) = legacy_plot_size(&svg);
        let plot_right = legacy_plot_left(&svg) + plot_w;
        assert_eq!(
            plot_right,
            600.0 - 16.0 - (chrome + super::VEGA_LEGEND_OFFSET + box_w),
            "{svg}"
        );
        assert_eq!(legacy_legend_title_x(&svg), plot_right + chrome + 18.0, "{svg}");
    }

    /// Number of `<line ... y1="0" x2="...">` x-gridlines a render draws —
    /// unique to the X gridline shape (`render_legacy`'s own emission at
    /// `y1="0"`), so a plain substring count reads `x_ticks.len()`.
    fn legacy_x_gridline_count(svg: &str) -> usize {
        svg.matches("y1=\"0\" x2=\"").count()
    }

    /// `render_legacy`'s x tick count must read `ax_x.ticks.count`
    /// (`render_fit`'s own source, fed into `d3rs::ticks::d3_ticks`), not a
    /// pixel-adaptive `round(plot_w/40)+1` formula blind to the authored
    /// count. Two renders differing only in `ticks.count`, same domain and
    /// plot width, must draw a different number of gridlines matching
    /// `d3_ticks`'s own output for each count — the pixel formula would
    /// draw the SAME count for both, since plot width does not change.
    #[test]
    fn legacy_arm_x_tick_count_reads_the_authored_count_not_a_pixel_formula() {
        let build = |count: f64| {
            x_threshold_payload_forcing_legacy(|json| {
                json["resolved"]["style"]["axis_x"]["grid"]["threshold"] = serde_json::Value::Null;
                json["resolved"]["style"]["axis_x"]["domain_min"] = serde_json::json!(0.0);
                json["resolved"]["style"]["axis_x"]["ticks"]["count"] = serde_json::json!(count);
                json["rows"] = serde_json::json!([
                    {"spend": 0.0, "pipeline": 0.15},
                    {"spend": 100.0, "pipeline": 1.0},
                ]);
            })
        };
        let expected_6 = d3rs::ticks::d3_ticks(0.0, 100.0, 6).len();
        let expected_3 = d3rs::ticks::d3_ticks(0.0, 100.0, 3).len();
        assert_ne!(expected_6, expected_3, "fixture counts must actually differ");
        let svg6 = render_json(build(6.0));
        let svg3 = render_json(build(3.0));
        assert_eq!(
            legacy_x_gridline_count(&svg6),
            expected_6,
            "ticks.count 6 must draw d3_ticks(0, 100, 6)'s own tick count: {svg6}"
        );
        assert_eq!(
            legacy_x_gridline_count(&svg3),
            expected_3,
            "ticks.count 3 must draw d3_ticks(0, 100, 3)'s own tick count: {svg3}"
        );
    }

    /// `render_fit` nices the domain (`d3_nice(lo, hi, 10)`) BEFORE feeding
    /// it to `d3_ticks` (p1's own data max, 67, nices up to 70) — feeding
    /// the raw, un-niced data max into `d3_ticks` directly strands the
    /// domain 3 short and drops the final tick production draws.
    #[test]
    fn legacy_arm_x_ticks_nice_the_domain_before_generating_tick_values() {
        let p = x_threshold_payload_forcing_legacy(|json| {
            json["resolved"]["style"]["axis_x"]["grid"]["threshold"] = serde_json::Value::Null;
            json["rows"] = serde_json::json!([
                {"spend": 12.0, "pipeline": 0.30},
                {"spend": 67.0, "pipeline": 0.41},
            ]);
        });
        let svg = render_json(p);
        let (nice_lo, nice_hi) = d3rs::ticks::d3_nice(0.0, 67.0, 10);
        let expected = d3rs::ticks::d3_ticks(nice_lo, nice_hi, 6).len();
        assert_eq!(
            legacy_x_gridline_count(&svg),
            expected,
            "must draw every tick of the NICED domain (through {nice_hi}), not just \
             the raw data max: {svg}"
        );
    }

    /// A point's own `<path transform="translate(cx,cy)">` x, in row order —
    /// the legacy arm's own point-paint format (`"translate({cx:.4},
    /// {cy:.4})"`).
    fn legacy_point_cxs(svg: &str) -> Vec<f64> {
        svg.match_indices("<path transform=\"translate(")
            .map(|(i, _)| {
                let after = &svg[i + "<path transform=\"translate(".len()..];
                let comma = after.find(',').expect("translate has a comma");
                after[..comma].parse::<f64>().expect("cx parses")
            })
            .collect()
    }

    /// `render_fit`'s own law (scatter.rs's continuous-x branch, "the niced
    /// extent IS the domain"): the x scale's own upper bound is the NICED
    /// high (`x_nice_hi`), not `d3_ticks`'s own last generated value — a
    /// count-6 tick step can land short of the niced round number whenever
    /// it is coarser than the count-10 nice step, stranding the scale (and
    /// every point) at the raw data max instead.
    #[test]
    fn legacy_arm_x_scale_domain_max_is_the_niced_high_not_the_last_tick() {
        let p = x_threshold_payload_forcing_legacy(|json| {
            json["resolved"]["style"]["axis_x"]["grid"]["threshold"] = serde_json::Value::Null;
            json["rows"] = serde_json::json!([
                {"spend": 12.0, "pipeline": 0.30},
                {"spend": 81.0, "pipeline": 0.41},
            ]);
        });
        let svg = render_json(p);
        let (_, nice_hi) = d3rs::ticks::d3_nice(0.0, 81.0, 10);
        let (pw, _) = legacy_plot_size(&svg);
        let cxs = legacy_point_cxs(&svg);
        let expected_cx = 81.0 / nice_hi * pw;
        assert!(
            (cxs[1] - expected_cx).abs() < 0.01,
            "x=81 must land at {expected_cx:.2} (scale domain 0..{nice_hi}), \
             not at the plot edge: cx={} svg={svg}",
            cxs[1]
        );
    }

    /// The legacy arm must paint the SAME axis title text `render_fit` and
    /// production do — `common::axis_title_text` (lowercase, tokenized),
    /// never a second `slug_to_display(field, case="title")` reading that
    /// humanizes the field name into Title Case.
    #[test]
    fn legacy_arm_axis_title_text_matches_common_axis_title_text_not_slug_to_display() {
        let mut p = make_scatter_payload(false);
        p["resolved"]["style"]["axis_x"]["scale"] =
            serde_json::json!({"continuous": {"domain": [0.0, 100.0]}});
        p["resolved"]["style"]["axis_x"]["title"]["visible"] = serde_json::json!(true);
        p["resolved"]["style"]["axis_y"]["title"]["visible"] = serde_json::json!(true);
        let svg = render_json(p);
        assert!(svg.contains(">spend<"), "x title reads lowercase 'spend': {svg}");
        assert!(svg.contains(">pipeline<"), "y title reads lowercase 'pipeline': {svg}");
        assert!(!svg.contains(">Spend<"), "must not paint slug_to_display's Title Case: {svg}");
        assert!(!svg.contains(">Pipeline<"), "must not paint slug_to_display's Title Case: {svg}");
    }

    /// Production's own-side `labels.align: right` on a right y axis bakes
    /// the label edge as `max_w + VALUE_AXIS_BREATHING_ROOM` (4), not
    /// `max_w + labels.padding` (6) — the unaligned default this arm keeps.
    /// The plot must therefore be exactly `labels.padding -
    /// VALUE_AXIS_BREATHING_ROOM` px WIDER when `labels.align: right` is
    /// authored than when it is not (a narrower right margin).
    #[test]
    fn legacy_arm_right_axis_own_side_aligned_labels_use_the_breathing_room_pad_not_labels_padding() {
        let build = |align: Option<&str>| {
            let mut p = make_scatter_payload(false);
            p["resolved"]["style"]["axis_x"]["scale"] =
                serde_json::json!({"continuous": {"domain": [0.0, 100.0]}});
            p["resolved"]["style"]["axis_y"]["labels"]["align"] =
                align.map_or(serde_json::Value::Null, |a| serde_json::json!(a));
            p
        };
        let unaligned = render_json(build(None));
        let aligned = render_json(build(Some("right")));
        let (pw_unaligned, _) = legacy_plot_size(&unaligned);
        let (pw_aligned, _) = legacy_plot_size(&aligned);
        assert_eq!(
            pw_aligned - pw_unaligned,
            2.0,
            "labels.padding (6) minus VALUE_AXIS_BREATHING_ROOM (4): \
             unaligned={pw_unaligned} aligned={pw_aligned}"
        );
    }

    /// A right-anchored (`text-anchor="end"`) y label's own x, off the
    /// legacy arm's y-label paint format (`"dy=\"0.35em\"
    /// text-anchor=\"end\""` is unique to it — the x-axis's own anchor
    /// carries no `dy`).
    fn legacy_y_label_x(svg: &str) -> f64 {
        let marker = "dy=\"0.35em\" text-anchor=\"end\"";
        let idx = svg.find(marker).expect("a y label is present");
        let before = &svg[..idx];
        let x_start = before.rfind("x=\"").expect("x attr present") + 3;
        let after = &svg[x_start..];
        let end = after.find('"').expect("x attr closes");
        after[..end].parse::<f64>().expect("x parses")
    }

    /// With no legend and no visible y title, the reserved right margin is
    /// `ceil(y_label_max_w + edge_pad)` — so the right-anchored label's
    /// own drawn edge (`plot_right + edge_pad + y_label_max_w`) must land
    /// on the padding boundary (`w - pad_r`) less the sub-pixel that
    /// integer extent rounds up by, the SAME edge the margin
    /// reserves. A paint site still reading the unaligned `labels.padding`
    /// while the margin reads `VALUE_AXIS_BREATHING_ROOM` overruns that
    /// edge by their difference — two readings of one law.
    #[test]
    fn legacy_arm_right_axis_label_anchor_matches_the_reserved_edge_when_own_side_aligned() {
        let mut p = make_scatter_payload(false);
        p["resolved"]["style"]["axis_x"]["scale"] =
            serde_json::json!({"continuous": {"domain": [0.0, 100.0]}});
        p["resolved"]["style"]["axis_y"]["labels"]["align"] = serde_json::json!("right");
        let svg = render_json(p);
        let anchor_x = legacy_y_label_x(&svg);
        let fonts = test_fonts();
        let chrome = ["150", "200", "250"]
            .iter()
            .map(|s| fonts.measure(s, TEST_FONT, 11.0).unwrap())
            .fold(0.0_f64, f64::max)
            + crate::common::VALUE_AXIS_BREATHING_ROOM;
        assert!(
            (anchor_x - (584.0 - (chrome.ceil() - chrome))).abs() < 0.005,
            "label edge must sit the integer chrome's rounding short of w(600) - \
             pad_r(16), the reserved margin's own edge: {svg}"
        );
    }

    /// The rotated y title's own drawn x, off the legacy arm's title paint
    /// format — unique to it (only the y title's `<text>` carries a
    /// `transform="rotate(`).
    fn legacy_y_title_x(svg: &str) -> f64 {
        let marker = "text-anchor=\"middle\" transform=\"rotate(";
        let idx = svg.find(marker).expect("a rotated y title is present");
        let before = &svg[..idx];
        let x_start = before.rfind("x=\"").expect("x attr present") + 3;
        let after = &svg[x_start..];
        let end = after.find('"').expect("x attr closes");
        after[..end].parse::<f64>().expect("x parses")
    }

    /// The legend title's own drawn x — the FIRST `text-anchor="start"`
    /// element after the last y-axis label (the legend paints after every
    /// axis element, and its own title precedes its per-category entries,
    /// which also carry `text-anchor="start"`).
    fn legacy_legend_title_x(svg: &str) -> f64 {
        let last_y_label = svg.rfind("dy=\"0.35em\"").expect("a y label is present");
        let after_y_labels = &svg[last_y_label..];
        let marker = "text-anchor=\"start\"";
        let rel_idx = after_y_labels.find(marker).expect("a legend title is present");
        let idx = last_y_label + rel_idx;
        let before = &svg[..idx];
        let x_start = before.rfind("x=\"").expect("x attr present") + 3;
        let after = &svg[x_start..];
        let end = after.find('"').expect("x attr closes");
        after[..end].parse::<f64>().expect("x parses")
    }

    /// The rotated title reads the SAME `y_label_edge_pad` the margin and
    /// the label anchor read (never a second `labels.padding` copy) —
    /// `title_x = label_edge + title.padding + rotated_axis_title_nudge(fontSize)`,
    /// Vega's own rotated-title law.
    #[test]
    fn legacy_arm_right_axis_title_x_reads_the_shared_edge_pad_and_nudge() {
        let mut p = make_scatter_payload(false);
        p["resolved"]["style"]["axis_x"]["scale"] =
            serde_json::json!({"continuous": {"domain": [0.0, 100.0]}});
        p["resolved"]["style"]["axis_y"]["labels"]["align"] = serde_json::json!("right");
        p["resolved"]["style"]["axis_y"]["title"]["visible"] = serde_json::json!(true);
        let svg = render_json(p);
        let label_edge_x = legacy_y_label_x(&svg);
        let title_x = legacy_y_title_x(&svg);
        assert_eq!(
            title_x,
            label_edge_x + 10.0 + crate::common::rotated_axis_title_nudge(11.0),
            "title x = label_edge + title.padding(10) + \
             rotated_axis_title_nudge(11): {svg}"
        );
    }

    /// The legend also reads the SAME edge pad — with an own-side-aligned
    /// axis, `leg_x = plot_right + ceil(max_w + VALUE_AXIS_BREATHING_ROOM) +
    /// VEGA_LEGEND_OFFSET`, which is `label_edge_x + 18` plus the sub-pixel
    /// the integer chrome rounds up by (the align-conditional edge pad,
    /// shared with the label anchor, cancels out of the two sums).
    #[test]
    fn legacy_arm_right_axis_legend_x_reads_the_shared_edge_pad() {
        let mut p = make_scatter_payload(true);
        p["resolved"]["style"]["axis_x"]["scale"] =
            serde_json::json!({"continuous": {"domain": [0.0, 100.0]}});
        p["resolved"]["style"]["axis_y"]["labels"]["align"] = serde_json::json!("right");
        let svg = render_json(p);
        let label_edge_x = legacy_y_label_x(&svg);
        let gap = legacy_legend_title_x(&svg) - label_edge_x;
        assert!(
            (super::VEGA_LEGEND_OFFSET..super::VEGA_LEGEND_OFFSET + 1.0).contains(&gap),
            "leg_x must track the SAME own-side edge pad the label anchor \
             reads, {gap} past it: {svg}"
        );
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
            svg.contains("aria-label=\"⁤SMB; pipeline: 140; spend: 18\""),
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
            "font": TEST_FONT, "text_mark_font": {"family": TEST_FONT, "size": 11.0}, "muted": null, "tones": null,
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
    /// is no number is refused rather than drawn as `NaN`.
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
        let payload: Payload = serde_json::from_value(json).unwrap();
        let err = render(&payload, &test_fonts()).expect_err("refuses");
        assert!(err.to_string().contains("is not a number"), "{err}");
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
    /// its own `null` band, which this port does not, so it refuses.
    #[test]
    fn a_null_category_refuses_by_name_rather_than_dropping_its_row() {
        let payload: Payload = serde_json::from_value(probe_payload(serde_json::json!([
            {"spend": "Alpha", "pipeline": 100.0, "segment": "A"},
            {"spend": "Bravo", "pipeline": 180.0, "segment": "B"},
            {"spend": null, "pipeline": 300.0, "segment": "Z"},
        ])))
        .unwrap();
        let err = render(&payload, &test_fonts()).expect_err("refuses");
        assert!(err.contains("a null category x is not supported"), "{err}");
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

    /// `axis_y.mirror: true` on the fit arm repeats the labels on the left edge;
    /// the legacy arm (a left primary) has no ghost model and refuses by name.
    #[test]
    fn mirror_repeats_the_labels_on_the_fit_arm_and_refuses_on_the_legacy_arm() {
        let mut json = make_scatter_payload(false);
        json["resolved"]["style"]["axis_y"]["mirror"] = serde_json::json!(true);
        let svg = render_json(json.clone());
        assert_eq!(svg.matches(">150</text>").count(), 2, "{svg}");
        json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
        let payload: Payload = serde_json::from_value(json).unwrap();
        let err = render(&payload, &test_fonts()).expect_err("legacy arm refuses mirror");
        assert!(err.contains("mirror"), "error names the unmodeled field: {err}");
    }

    /// A facet panel title that wraps even at the panel's full extent refuses by
    /// name rather than painting the raw text the composite would draw.
    #[test]
    fn a_facet_panel_title_that_wraps_refuses() {
        let font: crate::common::Font = serde_json::from_value(
            serde_json::json!({"family": TEST_FONT, "size": 11.0, "weight": "600"}),
        )
        .unwrap();
        let title = |visible: bool| crate::common::AxisTitle { font: font.clone(), padding: Some(10.0), visible: Some(visible) };
        let long = "A very long axis title that cannot fit in a narrow panel at all, however it wraps";
        let fonts = test_fonts();
        let err = crate::common::refuse_wrapping_facet_title("scatter", long, &title(true), 150.0, 1, &fonts)
            .expect_err("wraps");
        assert!(err.contains("axis title wider than its panel budget"), "{err}");
        crate::common::refuse_wrapping_facet_title("scatter", "Short", &title(true), 150.0, 1, &fonts).expect("fits");
        // Between the one-panel budget (150 - 72) and the seven-panel one
        // (150 - 72 / 7): production wraps it at one panel and not at seven.
        let between = "Commits per hour";
        crate::common::refuse_wrapping_facet_title("scatter", between, &title(true), 150.0, 1, &fonts)
            .expect_err("wraps at the one-panel budget");
        crate::common::refuse_wrapping_facet_title("scatter", between, &title(true), 150.0, 7, &fonts)
            .expect("fits the seven-panel budget");
        crate::common::refuse_wrapping_facet_title("scatter", long, &title(false), 150.0, 1, &fonts)
            .expect("a hidden title paints nothing");
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

    /// The unbaked-edge law is Vega's linear scale with its default `nice`:
    /// a scale term that reshapes that scale (`nice: false` skips the
    /// rounding, `padding`/`round` move the range, a non-linear type bends
    /// it) refuses by name instead of drawing the default niced scale.
    #[test]
    fn scale_terms_beside_an_unbaked_y_domain_refuse() {
        let ys = [-37.0, -7.0, 12.0, 25.0, 43.0];
        let ladder = [-40.0, -30.0, -20.0, -10.0, 0.0, 10.0, 20.0, 30.0, 40.0];
        for (scale, needle) in [
            (serde_json::json!({"nice": false}), "scale.nice"),
            (serde_json::json!({"padding": 10.0}), "scale.padding"),
            (serde_json::json!({"round": true}), "scale.round"),
            (serde_json::json!({"continuous": {"type": "pow"}}), "other than linear"),
        ] {
            let mut json = unbaked_y_payload(&ys, &ladder, false);
            json["resolved"]["style"]["axis_y"]["scale"] = scale;
            let payload: Payload = serde_json::from_value(json).unwrap();
            let err = render(&payload, &test_fonts()).expect_err("the fit arm refuses");
            assert!(err.contains(needle) && err.contains("fit arm"), "{needle}: {err}");
        }
    }

    /// Beside a baked edge Vega-Lite drops the default `nice`, so `nice: false`
    /// changes nothing and draws the baked bounds; an explicit `nice: true`
    /// (`emit_resolved_scale_vl`, `vl_field_maps.py:99-115`) makes Vega round
    /// -46.4..46.4 out to -50..50, which neither arm models, and `padding`
    /// moves the range: all refuse by name on both arms.
    #[test]
    fn scale_terms_beside_a_baked_y_edge_refuse_unless_they_change_nothing() {
        let ys = [-37.0, -7.0, 12.0, 25.0, 43.0];
        let ladder = [-40.0, -30.0, -20.0, -10.0, 0.0, 10.0, 20.0, 30.0, 40.0];
        let baked = |scale: serde_json::Value, left: bool| {
            let mut json = unbaked_y_payload(&ys, &ladder, false);
            let ay = &mut json["resolved"]["style"]["axis_y"];
            ay["domain_min"] = serde_json::json!(-46.4);
            ay["domain_max"] = serde_json::json!(46.4);
            ay["scale"] = scale;
            if left {
                ay["position"] = serde_json::json!("left");
            }
            json
        };
        for left in [false, true] {
            let arm = if left { "legacy arm" } else { "fit arm" };
            for (scale, needle) in [
                (serde_json::json!({"nice": true}), "scale.nice"),
                (serde_json::json!({"padding": 10.0}), "scale.padding"),
            ] {
                let payload: Payload = serde_json::from_value(baked(scale, left)).unwrap();
                let err = render(&payload, &test_fonts()).expect_err("the baked edge refuses");
                assert!(err.contains(needle) && err.contains(arm), "{arm} {needle}: {err}");
            }
            render_json(baked(serde_json::json!({"nice": false}), left));
        }
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

    /// A datum outside the authored pair is drawn past the plot edge by
    /// Vega; the fit arm does not model that, so it refuses by name.
    #[test]
    fn a_value_outside_an_authored_y_domain_refuses() {
        let mut json = make_scatter_payload(false);
        let ay = &mut json["resolved"]["style"]["axis_y"];
        ay["scale"] = serde_json::json!({"continuous": {"domain": [0.0, 200.0]}});
        ay["tick_values"] = serde_json::json!([0.0, 100.0, 200.0]);
        ay["domain_min"] = serde_json::Value::Null;
        ay["domain_max"] = serde_json::Value::Null;
        let payload: Payload = serde_json::from_value(json).unwrap();
        let err = render(&payload, &test_fonts()).expect_err("250 lies outside [0, 200]");
        assert_eq!(
            err,
            "scatter: y value 250 lies outside the authored domain [0, 200], which the fit \
             arm does not model"
        );
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

    /// The outside-the-domain check reads the niced pair Vega draws: with
    /// `[0, 487]` and `nice: true` a datum at 490 sits inside 0..500 and
    /// renders, while one at 505 lies outside it and refuses.
    #[test]
    fn fit_arm_outside_domain_check_reads_the_niced_pair() {
        let with_datum = |y: f64| {
            let mut json = with_authored_y_domain(make_scatter_payload(false), 0.0, 487.0);
            let ay = &mut json["resolved"]["style"]["axis_y"];
            ay["scale"]["nice"] = serde_json::json!(true);
            ay["tick_values"] = serde_json::json!([0.0, 100.0, 200.0, 300.0, 400.0, 500.0]);
            ay["domain_min"] = serde_json::Value::Null;
            ay["domain_max"] = serde_json::Value::Null;
            json["rows"]
                .as_array_mut()
                .unwrap()
                .push(serde_json::json!({"spend": 25.0, "pipeline": y}));
            serde_json::from_value::<Payload>(json).unwrap()
        };
        let inside = render(&with_datum(490.0), &test_fonts()).expect("490 is inside 0..500");
        assert!(inside.contains(">500</text>"), "{inside}");
        let err = render(&with_datum(505.0), &test_fonts()).expect_err("505 is outside 0..500");
        assert!(err.contains("y value 505 lies outside the authored domain [0, 500]"), "{err}");
    }

    /// Scale terms that reshape an authored domain at render time
    /// (`padding`/`round` move the range) are not modeled beside it.
    #[test]
    fn scale_terms_beside_an_authored_y_domain_refuse() {
        for (term, value) in [
            ("padding", serde_json::json!(10.0)),
            ("round", serde_json::json!(true)),
        ] {
            let mut json = make_scatter_payload(false);
            let ay = &mut json["resolved"]["style"]["axis_y"];
            ay["scale"] = serde_json::json!({"continuous": {"domain": [0.0, 400.0]}});
            ay["scale"][term] = value;
            ay["tick_values"] = serde_json::json!([0.0, 100.0, 200.0, 300.0, 400.0]);
            ay["domain_min"] = serde_json::Value::Null;
            ay["domain_max"] = serde_json::Value::Null;
            let payload: Payload = serde_json::from_value(json).unwrap();
            let err = render(&payload, &test_fonts()).expect_err(term);
            assert!(
                err.contains(&format!("scale.{term}")) && err.contains("authored y-axis domain"),
                "{term}: {err}"
            );
        }
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
    fn legacy_arm_board_slot_wins_the_point_fill_and_legend_swatch() {
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

    /// An authored x scale transform the fit path doesn't model (here an
    /// authored `scale.continuous.domain`) routes to the legacy arm.
    #[test]
    fn authored_x_scale_domain_routes_to_legacy_arm() {
        let mut json = make_scatter_payload(false);
        json["resolved"]["style"]["axis_x"]["scale"] =
            serde_json::json!({"continuous": {"domain": [10.0, 50.0]}});
        let svg = render_json(json);
        assert!(svg.contains("dy=\"0.35em\""), "legacy arm rendered: {svg}");
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

    /// Regression for the round-1 review's HIGH-2: `SIZE_LEGEND_TICK_COUNT`
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

    /// Regression for HIGH-2: a fractional domain's tick STEP (0.2) implies
    /// 1 decimal of precision (d3-format's `precisionFixed`) — a mutant
    /// that forces `tick_precision` to always return 0 would draw `"0"`,
    /// `"0"` (two zeros — `0.2` and `0.0` both round to `0` decimals) where
    /// this expects `"0.0"`, `"0.2"`.
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
            "font": TEST_FONT, "text_mark_font": {"family": TEST_FONT, "size": 11.0}, "muted": null, "tones": null,
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
            "font": TEST_FONT, "text_mark_font": {"family": TEST_FONT, "size": 11.0}, "muted": null, "tones": null,
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

    /// A non-numeric size value refuses rather than silently coercing or
    /// dropping the row.
    #[test]
    fn non_numeric_size_value_refuses() {
        let mut json = make_scatter_payload_with_size(true);
        json["rows"][0]["deals"] = serde_json::json!("lots");
        let err = render_err(json);
        assert!(err.contains("size") && err.contains("numeric"), "{err}");
    }

    /// An all-zero size column is degenerate (zero-anchored domain
    /// `[min(0, 0), max(0, 0)] = [0, 0]`, `hi <= lo`) — refuses rather than
    /// dividing by a zero span (round-1 review's MEDIUM-3: this branch had
    /// no test).
    #[test]
    fn degenerate_size_domain_refuses() {
        let json = make_scatter_payload_with_size_values(&[0.0, 0.0, 0.0]);
        let err = render_err(json);
        assert!(err.contains("size domain is degenerate"), "{err}");
    }

    /// A nominal x swapped onto a dot-plot fixture (whose axis_y still
    /// carries the dot plot's own empty ladder) lands outside the fit
    /// shape — `y_measure` needs a baked tick ladder this fixture never
    /// gives it — so it still refuses, on the same "outside the fit
    /// shape" message a nominal y gets, not a nominal-x-specific one: a
    /// nominal x is itself supported now (see
    /// `nominal_x_places_marks_on_the_point_scale_bands` below), only this
    /// particular payload isn't shaped for it.
    #[test]
    fn nominal_x_outside_the_fit_shape_refuses() {
        let mut json = make_dot_plot_payload(dot_rows("Charlie"));
        json["resolved"]["x"] = serde_json::json!("team");
        json["resolved"]["y"] = serde_json::json!("median_response_hours");
        let err = render_err(json);
        assert!(err.contains("dot/band plot"), "{err}");
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

    /// A dot plot outside the fit shape (here a left y axis) refuses: the
    /// legacy arm's point loop reads a numeric y and would draw nothing.
    #[test]
    fn a_dot_plot_outside_the_fit_shape_refuses() {
        let mut json = make_dot_plot_payload(dot_rows("Charlie"));
        json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
        let err = render_err(json);
        assert!(err.contains("dot/band plot"), "{err}");
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
            "title": {"font": {"family": TEST_FONT, "size": 12.0, "weight": "600", "color": "#313233"}},
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
        assert!(svg.contains(">total cost<"), "{svg}");
        assert!(svg.contains(">unit price<"), "{svg}");
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
                "aria-label=\"team: Alpha; unit price, total cost: 10; Series: unit price\""
            ),
            "{svg}"
        );
        assert!(!svg.contains('\u{2064}'), "no ROLE_HEADER_SWATCHED marker: {svg}");
    }

    /// `wide_measures` beside an authored `color:` dimension composes the
    /// fold's `<value> - <measure>` series, humanized on the measure part.
    #[test]
    fn wide_measures_with_an_authored_color_dimension_compose_series_labels() {
        let mut json = make_wide_dot_band_payload();
        json["resolved"]["color"] = serde_json::json!("team");
        let svg = render_json(json);
        assert!(svg.contains(">Alpha - unit price</text>"), "{svg}");
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

    /// The fold tooltip formats a nominal x only, so a quantitative x refuses.
    #[test]
    fn wide_measures_with_a_quantitative_x_refuse() {
        let mut json = make_wide_quantitative_x_payload();
        json["resolved"]["style"]["axis_x"]["labels"]["format"] = serde_json::json!(",.2f");
        let err = render_err(json);
        assert!(err.contains("wide_measures with a quantitative or date x"), "{err}");
    }

    /// Point value labels would paint one color over a per-series fold.
    #[test]
    fn wide_measures_with_point_labels_refuse() {
        let mut json = make_wide_dot_band_payload();
        json["resolved"]["style"]["point_mark"]["labels"] = serde_json::json!({"visible": true});
        let err = render_err(json);
        assert!(err.contains("point value labels with wide_measures"), "{err}");
    }

    /// Outside the fit shape the legacy arm would leak the synthetic fold
    /// field names as titles.
    #[test]
    fn wide_measures_outside_the_fit_shape_refuse() {
        let mut json = make_wide_dot_band_payload();
        json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
        let err = render_err(json);
        assert!(err.contains("wide_measures outside production's fit shape"), "{err}");
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
            let end = svg.find(">department<").unwrap();
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
            let end = svg.find(">team<").unwrap();
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
            let end = svg.find(">team<").unwrap();
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

    /// Vega's domain sort is `{op: min}` over the sort field; a non-numeric
    /// sort key is unported and refuses rather than guessing an order.
    #[test]
    fn a_dot_plot_sorted_by_a_non_numeric_field_refuses() {
        let mut json = make_dot_plot_payload(dot_rows("Charlie"));
        json["resolved"]["sort"] = serde_json::json!({"by": "team", "order": "asc"});
        let err = render_err(json);
        assert!(err.contains("sort"), "{err}");
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

    /// A time `labels.format` on a categorical y is production's UTC
    /// labelExpr: it relabels every band. Unported — so it refuses rather
    /// than drawing the raw category strings under an authored date format.
    #[test]
    fn a_time_label_format_on_a_categorical_y_refuses() {
        let mut json = make_dot_plot_payload(dot_rows("Charlie"));
        json["resolved"]["style"]["axis_y"]["labels"]["format"] = serde_json::json!("%b %Y");
        let err = render_err(json);
        assert!(err.contains("time"), "{err}");
        // A number format is dropped by production, not routed — the
        // category strings render as themselves.
        let mut ok = make_dot_plot_payload(dot_rows("Charlie"));
        ok["resolved"]["style"]["axis_y"]["labels"]["format"] = serde_json::json!(".3~s");
        assert_eq!(band_labels(&render_json(ok)), ["Bravo", "Charlie", "Alpha"]);
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
        assert!((attr(tag, "x2") - plot_w).abs() < 1e-6, "spans full plot width: {tag}");
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
        assert!(attr(tag, "x1").abs() < 1e-6, "spans the full plot width: {tag}");
        assert!((attr(tag, "x2") - plot_w).abs() < 1e-6, "{tag}");
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

    /// Legacy arm's plot box, read off any gridline's constant far edge — an
    /// X (vertical) gridline's `y2` is `plot_h`, a Y (horizontal) gridline's
    /// `x2` is `plot_w` (`render_legacy`'s gridlines always span the full
    /// opposite dimension, whichever tick they're drawn at).
    fn legacy_plot_size(svg: &str) -> (f64, f64) {
        let pw = svg
            .split("<line ")
            .skip(1)
            .find(|t| t.starts_with("x1=\"0\" y1=\""))
            .map(|t| attr(t, "x2"))
            .expect("a y gridline gives plot_w");
        let ph = svg
            .split("<line ")
            .skip(1)
            .find(|t| t.contains("y1=\"0\" x2="))
            .map(|t| attr(t, "y2"))
            .expect("an x gridline gives plot_h");
        (pw, ph)
    }

    /// `BaselineFeature.applies_to` admits every cartesian family but
    /// heatmap (`baseline.py`), so a measure/measure scatter whose y
    /// straddles 0, unanchored, still draws production's zero rule even
    /// when routed to this legacy arm (here, by a left y axis — mirrors
    /// `legacy_arm_no_color_channel_paints_single_series_fill`'s own
    /// routing) — `render_legacy` must carry the same gate `render_fit`
    /// does (`playground/threshold-baseline-proof::p1_scatter_y_only`).
    #[test]
    fn legacy_arm_emits_the_y_zero_rule_when_the_domain_straddles_zero_unanchored() {
        let mut json = make_scatter_payload(false);
        json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
        json["rows"] = serde_json::json!([
            {"spend": 12.0, "pipeline": -30.0},
            {"spend": 24.0, "pipeline": -8.0},
            {"spend": 38.0, "pipeline": 6.0},
            {"spend": 52.0, "pipeline": 25.0},
            {"spend": 67.0, "pipeline": 41.0},
        ]);
        let ay = &mut json["resolved"]["style"]["axis_y"];
        ay["domain_min"] = serde_json::json!(-35.68);
        ay["domain_max"] = serde_json::json!(46.68);
        ay["tick_values"] = serde_json::json!([]);
        set_zero_style(&mut json, true);
        let svg = render_json(json);
        let rule_tags: Vec<&str> =
            svg.split("<line ").skip(1).filter(|t| t.contains(ZERO_RULE_COLOR)).collect();
        assert_eq!(
            rule_tags.len(),
            1,
            "exactly the y=0 rule fires (spend never reaches 0 on x): {svg}"
        );
        let (_, plot_h) = legacy_plot_size(&svg);
        let expected_y = plot_h * 46.68 / (46.68 - (-35.68));
        assert!(
            (attr(rule_tags[0], "y1") - expected_y).abs() < 0.05,
            "the rule sits at the y-scale position of 0: expected {expected_y}, got {}: {svg}",
            attr(rule_tags[0], "y1")
        );
    }

    // ── Legacy arm: an unbaked measure axis is Vega's own linear domain ────

    /// The `(lo, hi)` a legacy-arm axis draws, recovered from two marks at
    /// data values `v0 != v1` whose distances from the axis origin are
    /// `p0`/`p1` along an axis of pixel length `extent`: the position is
    /// `extent * (v - lo) / (hi - lo)`.
    fn recovered_domain(extent: f64, (p0, v0): (f64, f64), (p1, v1): (f64, f64)) -> (f64, f64) {
        let span = (v1 - v0) * extent / (p1 - p0);
        let lo = v0 - p0 * span / extent;
        (lo, lo + span)
    }

    /// A left-axis (legacy-arm) scatter with no baked domain or ladder on
    /// either axis, drawing `rows` of `(x, y)` pairs.
    fn legacy_unbaked_payload(rows: &[(f64, f64)]) -> serde_json::Value {
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

    /// Both domains the legacy arm drew for `svg`, from its first and last
    /// marks (x, then y).
    fn legacy_drawn_domains(svg: &str, first: (f64, f64), last: (f64, f64)) -> ((f64, f64), (f64, f64)) {
        let (plot_w, plot_h) = legacy_plot_size(svg);
        let marks = symbol_centers(svg);
        let (a, b) = (marks[0], marks[marks.len() - 1]);
        (
            recovered_domain(plot_w, (a.0, first.0), (b.0, last.0)),
            recovered_domain(plot_h, (plot_h - a.1, first.1), (plot_h - b.1, last.1)),
        )
    }

    /// An unbaked axis is Vega's linear scale over the zero-joined data
    /// extent, niced at d3's count 10 (`zero: true` and `nice: true` are the
    /// Vega-Lite defaults on a quantitative x/y). Probed against production
    /// with x in -33..37 and y in -37..43: the compiled scales are
    /// `{zero: true, nice: true}` and the rendered axes span -40..40 and
    /// -40..50; negative values extend the axis, they are never floored.
    #[test]
    fn legacy_arm_extends_an_unbaked_axis_over_negative_data_and_nices_it() {
        let rows = [(-33.0, -37.0), (-11.0, -7.0), (5.0, 12.0), (22.0, 25.0), (37.0, 43.0)];
        let svg = render_json(legacy_unbaked_payload(&rows));
        let (x, y) = legacy_drawn_domains(&svg, (-33.0, -37.0), (37.0, 43.0));
        assert!((x.0 + 40.0).abs() < 0.05 && (x.1 - 40.0).abs() < 0.05, "x domain {x:?}: {svg}");
        assert!((y.0 + 40.0).abs() < 0.05 && (y.1 - 50.0).abs() < 0.05, "y domain {y:?}: {svg}");
    }

    /// Positive-only data keeps zero as the floor: `zero: true` joins 0 to
    /// the extent, then nice runs (x 18..39 -> 0..40, y 140..250 -> 0..260;
    /// production draws the same 0..260 for a zero-anchored y over 0..250).
    #[test]
    fn legacy_arm_zero_joins_an_unbaked_axis_of_positive_data() {
        let rows = [(18.0, 140.0), (27.0, 200.0), (39.0, 250.0)];
        let svg = render_json(legacy_unbaked_payload(&rows));
        let (x, y) = legacy_drawn_domains(&svg, (18.0, 140.0), (39.0, 250.0));
        assert!(x.0.abs() < 0.05 && (x.1 - 40.0).abs() < 0.05, "x domain {x:?}: {svg}");
        assert!(y.0.abs() < 0.05 && (y.1 - 260.0).abs() < 0.05, "y domain {y:?}: {svg}");
    }

    /// `scale.continuous.zero: false` drops the zero join, so the unbaked
    /// domain is the niced data extent alone. Probed against production
    /// (x 22..47, y 20..43, `zero: false` on both): the axes span 22..48 and
    /// 20..44.
    #[test]
    fn legacy_arm_does_not_zero_join_an_axis_with_zero_false() {
        let rows = [(22.0, 20.0), (31.0, 27.0), (35.0, 30.0), (41.0, 38.0), (47.0, 43.0)];
        let mut json = legacy_unbaked_payload(&rows);
        json["resolved"]["style"]["axis_x"]["scale"] = serde_json::json!({"continuous": {"zero": false}});
        json["resolved"]["style"]["axis_y"]["scale"] = serde_json::json!({"continuous": {"zero": false}});
        let svg = render_json(json);
        let (x, y) = legacy_drawn_domains(&svg, (22.0, 20.0), (47.0, 43.0));
        assert!((x.0 - 22.0).abs() < 0.05 && (x.1 - 48.0).abs() < 0.05, "x domain {x:?}: {svg}");
        assert!((y.0 - 20.0).abs() < 0.05 && (y.1 - 44.0).abs() < 0.05, "y domain {y:?}: {svg}");
    }

    /// Vega-Lite's `mark.invalid` default for a point mark is `"filter"`
    /// (`mark.ts:351` + `normalizeInvalidDataMode.ts:9-10`, vega-lite
    /// v6.4.1): `filterinvalid.ts:36-55,85-99` drops a row whose `x` or `y`
    /// is not a finite number before the scale domain and the mark alike.
    /// Probed against production with rows (10,100) (20,180) (30,220)
    /// (40,300) plus (null,900) and (400,null): the axes span x 0..40 and
    /// y 0..300 — neither null-coordinate row stretches an axis, though each
    /// carries the largest value of its other column.
    #[test]
    fn legacy_arm_domains_read_only_rows_with_both_coordinates() {
        let mut json = legacy_unbaked_payload(&[(10.0, 100.0), (20.0, 180.0), (30.0, 220.0), (40.0, 300.0)]);
        let rows = json["rows"].as_array_mut().unwrap();
        rows.push(serde_json::json!({"spend": null, "pipeline": 900.0}));
        rows.push(serde_json::json!({"spend": 400.0, "pipeline": null}));
        let svg = render_json(json);
        let (x, y) = legacy_drawn_domains(&svg, (10.0, 100.0), (40.0, 300.0));
        assert!(x.0.abs() < 0.05 && (x.1 - 40.0).abs() < 0.05, "x domain {x:?}: {svg}");
        assert!(y.0.abs() < 0.05 && (y.1 - 300.0).abs() < 0.05, "y domain {y:?}: {svg}");
    }

    /// The color scale reads the filtered dataset too (`filterinvalid.ts`
    /// filters the unit's data source ahead of every scale): a series that
    /// appears only on a dropped null-coordinate row keeps no legend entry
    /// and no palette slot. Probed against production with series S, A, B
    /// plus (null, 300, "Z"): the legend lists S, A, B.
    #[test]
    fn legacy_arm_legend_omits_a_series_only_on_a_dropped_row() {
        let mut json = legacy_unbaked_payload(&[(10.0, 100.0), (20.0, 180.0), (30.0, 220.0)]);
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
    fn legacy_arm_null_coordinate_row_never_trips_the_outside_domain_refusal() {
        let mut json = legacy_unbaked_payload(&[(10.0, 100.0), (40.0, 300.0)]);
        json["rows"]
            .as_array_mut()
            .unwrap()
            .push(serde_json::json!({"spend": null, "pipeline": 900.0}));
        json["resolved"]["style"]["axis_y"]["scale"] =
            serde_json::json!({"continuous": {"domain": [0.0, 300.0]}});
        let svg = render_json(json);
        assert_eq!(symbol_centers(&svg).len(), 2, "{svg}");
    }

    /// A baked edge reaches Vega verbatim (Vega-Lite drops `nice` once
    /// `domainMin`/`domainMax` is set), and the other edge is the zero-joined
    /// extent, exact: a baked `domain_min` of -45 over y in -37..43 leaves
    /// the top at 43, not the niced 50.
    #[test]
    fn legacy_arm_baked_edge_wins_and_the_other_edge_is_the_exact_extent() {
        let rows = [(18.0, -37.0), (27.0, -7.0), (39.0, 43.0)];
        let mut json = legacy_unbaked_payload(&rows);
        json["resolved"]["style"]["axis_y"]["domain_min"] = serde_json::json!(-45.0);
        let svg = render_json(json);
        let (_, y) = legacy_drawn_domains(&svg, (18.0, -37.0), (39.0, 43.0));
        assert!((y.0 + 45.0).abs() < 0.05 && (y.1 - 43.0).abs() < 0.05, "y domain {y:?}: {svg}");
    }

    /// The right edge of a left-axis scatter with no legend hosts no axis:
    /// Vega's `autosize: fit` reserves the same measured `AXIS_FREE_EDGE_PAD`
    /// there as on any axis-free edge (probed against production, x in
    /// -33..37 with the y axis on the left: plot right edge 2px inside the
    /// card padding).
    #[test]
    fn legacy_arm_left_axis_right_edge_is_the_axis_free_pad() {
        let rows = [(-33.0, -37.0), (-11.0, -7.0), (5.0, 12.0), (22.0, 25.0), (37.0, 43.0)];
        let svg = render_json(legacy_unbaked_payload(&rows));
        let (plot_w, _) = legacy_plot_size(&svg);
        assert_eq!(
            legacy_plot_left(&svg) + plot_w,
            600.0 - 16.0 - crate::common::AXIS_FREE_EDGE_PAD,
            "card width - padding - the shared axis-free-edge inset: {svg}"
        );
    }

    /// A point exactly on the x ceiling overhangs the axis-free right edge
    /// by its own ink, so the edge widens to the ceil'd radius (probed: x
    /// reaching the niced 40 leaves 4px, the ceil of the default symbol's
    /// 3.09px radius, against 2px for x ending at 37).
    #[test]
    fn legacy_arm_left_axis_right_edge_widens_for_a_point_on_the_x_ceiling() {
        let rows = [(-33.0, -37.0), (-11.0, -7.0), (5.0, 12.0), (22.0, 25.0), (40.0, 43.0)];
        let svg = render_json(legacy_unbaked_payload(&rows));
        let (plot_w, _) = legacy_plot_size(&svg);
        assert_eq!(
            legacy_plot_left(&svg) + plot_w,
            600.0 - 16.0 - crate::common::symbol_radius(60.0).ceil(),
            "the ceiling point's own ceil'd radius: {svg}"
        );
    }

    /// A point just INSIDE the x ceiling still overhangs the axis-free right
    /// edge by its own ink, the shape the exact-ceiling gate is blind to:
    /// refuse rather than paint the plot rect too wide for it.
    #[test]
    fn legacy_arm_left_axis_refuses_a_point_just_inside_the_x_ceiling() {
        let rows = [(-33.0, -37.0), (-11.0, -7.0), (5.0, 12.0), (22.0, 25.0), (39.9, 43.0)];
        let payload: Payload = serde_json::from_value(legacy_unbaked_payload(&rows)).unwrap();
        let err = render(&payload, &test_fonts()).expect_err("an unmodeled ink overhang refuses");
        assert!(err.contains("x-axis ceiling"), "{err}");
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

    /// Same law on the legacy arm — forced here by an authored x scale
    /// transform (mirrors `authored_x_scale_domain_routes_to_legacy_arm`),
    /// the same gate `render_fit` uses via `x_threshold_fires`.
    #[test]
    fn legacy_arm_emits_x_threshold_rules_too() {
        let mut json = x_threshold_payload(|_| {});
        json["resolved"]["style"]["axis_x"]["scale"] =
            serde_json::json!({"continuous": {"domain": [0.0, 1.0]}});
        let svg = render_json(json);
        assert_eq!(
            x_rule_tags(&svg).len(),
            2,
            "both x=0 and x=1 rules fire on the legacy arm too: {svg}"
        );
        let (plot_w, _) = legacy_plot_size(&svg);
        let tags = x_rule_tags(&svg);
        for tag in &tags {
            let x = attr(tag, "x1");
            assert!((-0.5..=plot_w + 0.5).contains(&x), "rule inside the plot rect: {tag}");
        }
    }

    /// An authored `scale.continuous.domain` reaches Vega verbatim and
    /// unniced (`emitters/scatter.py:282` for x, `:354-358` for y; probed:
    /// x [-50, 50] and y [-50, 60] over x -33..37, y -37..43 draw exactly
    /// those spans), so the x=1 rule of an authored x domain [0, 1] sits on
    /// the plot's right edge.
    #[test]
    fn legacy_arm_places_the_x_one_rule_on_the_edge_of_an_authored_domain() {
        let mut json = x_threshold_payload(|_| {});
        json["rows"] = serde_json::json!([
            {"spend": 0.0, "pipeline": 0.15},
            {"spend": 0.1, "pipeline": 0.40},
            {"spend": 0.2, "pipeline": 0.62},
            {"spend": 0.3, "pipeline": 0.85},
            {"spend": 0.5, "pipeline": 1.0},
        ]);
        json["resolved"]["style"]["axis_x"]["scale"] =
            serde_json::json!({"continuous": {"domain": [0.0, 1.0]}});
        let svg = render_json(json);
        let (plot_w, _) = legacy_plot_size(&svg);
        let tags = x_rule_tags(&svg);
        assert_eq!(tags.len(), 2, "{svg}");
        assert!((attr(tags[1], "x1") - plot_w).abs() < 0.01, "x=1 at the right edge: {tags:?}");
    }

    /// Same law on y: an authored domain [0, 1.2] holds the y=1 unity rule
    /// at 1/6 of the plot height from the top.
    #[test]
    fn legacy_arm_places_the_y_unity_rule_inside_an_authored_domain() {
        let mut json = make_scatter_payload(false);
        json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
        json["rows"] = serde_json::json!([
            {"spend": 18.0, "pipeline": 0.2},
            {"spend": 27.0, "pipeline": 0.5},
            {"spend": 39.0, "pipeline": 0.8},
        ]);
        let ay = &mut json["resolved"]["style"]["axis_y"];
        ay["labels"]["format"] = serde_json::json!(".0%");
        ay["grid"]["visible"] = serde_json::json!(true);
        ay["grid"]["threshold"] = serde_json::json!({"color": "#123456", "width": 2.0});
        ay["domain_min"] = serde_json::Value::Null;
        ay["domain_max"] = serde_json::Value::Null;
        ay["tick_values"] = serde_json::json!([]);
        ay["scale"] = serde_json::json!({"continuous": {"domain": [0.0, 1.2]}});
        let svg = render_json(json);
        let (_, plot_h) = legacy_plot_size(&svg);
        // The y=0 rule is painted first, the y=1 unity rule second.
        let rule = svg
            .split("<line ")
            .skip(1)
            .filter(|t| t.contains("#123456"))
            .nth(1)
            .expect("the y=1 unity rule is painted");
        assert!((attr(rule, "y1") - plot_h * 0.2 / 1.2).abs() < 0.01, "{rule}");
    }

    /// Authored domains reach the legacy arm verbatim on both axes, wider
    /// than the data and unniced, whatever the baked ladder says.
    #[test]
    fn legacy_arm_honors_an_authored_domain_on_both_axes_unniced() {
        let rows = [(-33.0, -37.0), (-11.0, -7.0), (5.0, 12.0), (22.0, 25.0), (37.0, 43.0)];
        let mut json = legacy_unbaked_payload(&rows);
        json["resolved"]["style"]["axis_x"]["scale"] =
            serde_json::json!({"continuous": {"domain": [-50.0, 50.0]}});
        json["resolved"]["style"]["axis_y"]["scale"] =
            serde_json::json!({"continuous": {"domain": [-50.0, 60.0]}});
        let svg = render_json(json);
        let (x, y) = legacy_drawn_domains(&svg, (-33.0, -37.0), (37.0, 43.0));
        assert!((x.0 + 50.0).abs() < 0.05 && (x.1 - 50.0).abs() < 0.05, "x domain {x:?}: {svg}");
        assert!((y.0 + 50.0).abs() < 0.05 && (y.1 - 60.0).abs() < 0.05, "y domain {y:?}: {svg}");
    }

    /// `scale.nice: true` beside an authored pair re-rounds it outward at d3's
    /// count 10 (`configureDomain`, `vega.js:21556-21559`): y `[0, 487]` draws
    /// 0..500, x `[-47, 47]` draws -50..50, the same step bar applies.
    #[test]
    fn legacy_arm_nices_an_authored_domain_beside_scale_nice_true() {
        let rows = [(-33.0, 12.0), (-11.0, 100.0), (5.0, 220.0), (22.0, 300.0), (37.0, 400.0)];
        let mut json = legacy_unbaked_payload(&rows);
        json["resolved"]["style"]["axis_x"]["scale"] =
            serde_json::json!({"nice": true, "continuous": {"domain": [-47.0, 47.0]}});
        json["resolved"]["style"]["axis_y"]["scale"] =
            serde_json::json!({"nice": true, "continuous": {"domain": [0.0, 487.0]}});
        let svg = render_json(json);
        let (x, y) = legacy_drawn_domains(&svg, (-33.0, 12.0), (37.0, 400.0));
        assert!((x.0 + 50.0).abs() < 0.05 && (x.1 - 50.0).abs() < 0.05, "x domain {x:?}: {svg}");
        assert!(y.0.abs() < 0.05 && (y.1 - 500.0).abs() < 0.05, "y domain {y:?}: {svg}");
        assert!(svg.contains(">500</text>"), "the niced top rung is missing: {svg}");
    }

    /// Scale keys the legacy arm does not draw refuse by name instead of
    /// drawing the default niced linear scale: a non-linear type, `nice:
    /// false`, a nonzero `padding`, `round`, `x_reverse`, a non-numeric or
    /// non-ascending authored domain.
    #[test]
    fn legacy_arm_refuses_unmodeled_scale_keys_by_name() {
        let rows = [(-33.0, -37.0), (37.0, 43.0)];
        for (axis, scale, needle) in [
            ("axis_x", serde_json::json!({"nice": false}), "scale.nice"),
            ("axis_y", serde_json::json!({"nice": false}), "scale.nice"),
            ("axis_x", serde_json::json!({"padding": 0.25}), "scale.padding"),
            ("axis_x", serde_json::json!({"round": true}), "scale.round"),
            ("axis_x", serde_json::json!({"x_reverse": true}), "scale.x_reverse"),
            ("axis_x", serde_json::json!({"continuous": {"type": "pow"}}), "other than linear"),
            ("axis_y", serde_json::json!({"continuous": {"type": "symlog"}}), "other than linear"),
            ("axis_x", serde_json::json!({"continuous": {"domain": ["a", "b"]}}), "numeric pair"),
            ("axis_x", serde_json::json!({"continuous": {"domain": [50.0, -50.0]}}), "not ascending"),
        ] {
            let mut json = legacy_unbaked_payload(&rows);
            json["resolved"]["style"][axis]["scale"] = scale;
            let payload: Payload = serde_json::from_value(json).unwrap();
            let err = render(&payload, &test_fonts()).expect_err("the legacy arm refuses");
            assert!(err.contains(needle), "{axis}: {err}");
        }
    }

    /// A row outside the drawn domain refuses by name on either axis, authored
    /// or computed: production's `autosize: fit` takes such a mark's ink into
    /// the plot (probed: x domain [-50, 50] with data to 40 puts x=50 at 363px
    /// of a 400px card, with data to 90 at 199px), which this arm would not.
    #[test]
    fn legacy_arm_refuses_a_row_outside_the_drawn_domain() {
        let rows = [(-33.0, -37.0), (-11.0, -7.0), (5.0, 12.0), (22.0, 25.0), (37.0, 43.0)];
        for (axis, domain, needle) in [
            ("axis_x", [-20.0, 50.0], "\"spend\" value -33"),
            ("axis_y", [-50.0, 40.0], "\"pipeline\" value 43"),
        ] {
            let mut json = legacy_unbaked_payload(&rows);
            json["resolved"]["style"][axis]["scale"] =
                serde_json::json!({"continuous": {"domain": domain}});
            let payload: Payload = serde_json::from_value(json).unwrap();
            let err = render(&payload, &test_fonts()).expect_err("an out-of-domain mark refuses");
            assert!(err.contains(needle) && err.contains("outside the drawn domain"), "{err}");
        }
    }

    /// A constant column with no baked edge draws on a one-tick axis (probed:
    /// y constant 10 with `{zero: false, nice: true}`, height 200): d3 maps
    /// every value to the range midpoint. A constant x column still refuses.
    #[test]
    fn legacy_arm_centers_a_constant_y_column_on_a_one_tick_axis() {
        let rows = [(18.0, 7.0), (27.0, 7.0), (39.0, 7.0)];
        let mut json = legacy_unbaked_payload(&rows);
        json["resolved"]["style"]["axis_y"]["scale"] =
            serde_json::json!({"headroom": 0.08, "continuous": {"zero": false}});
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("a constant y column renders");
        assert_eq!(svg.matches(">7</text>").count(), 1, "one tick: {svg}");
        // Measuring the plot and painting its marks read one zero-extent rule
        // (`LinearScale`): a second copy dividing by the span would put NaN here.
        assert!(!svg.contains("NaN"), "{svg}");

        let mut json = legacy_unbaked_payload(&[(18.0, 10.0), (18.0, 12.0)]);
        json["resolved"]["style"]["axis_x"]["scale"] =
            serde_json::json!({"headroom": 0.08, "continuous": {"zero": false}});
        let payload: Payload = serde_json::from_value(json).unwrap();
        let err = render(&payload, &test_fonts()).expect_err("a constant x column refuses");
        assert!(err.contains("a constant column"), "{err}");
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
        assert!(svg.contains("aria-label=\"⁤SMB; pipeline: US$140; spend: 18\""), "{svg}");
    }

    /// A quantitative x that names a format of its own (`x_tooltip_format`)
    /// paints that slot, affix included.
    #[test]
    fn a_quantitative_x_paints_its_own_tooltip_format() {
        let mut p = make_scatter_payload(true);
        p["resolved"]["style"]["tooltip_format"] = serde_json::json!({"spec": ",.2~f", "prefix": "US$"});
        p["resolved"]["x_tooltip_format"] = serde_json::json!({"spec": ",.1f", "suffix": " k"});
        let svg = render_json(p);
        assert!(svg.contains("aria-label=\"⁤SMB; pipeline: US$140; spend: 18.0 k\""), "{svg}");
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
            svg.contains("aria-label=\"team: Alpha; unit price, total cost: US$10; Series: unit price\""),
            "{svg}"
        );
    }

    /// The scatter paints no affix on either axis: it refuses by name.
    #[test]
    fn an_axis_affix_refuses_by_name() {
        for axis in ["axis_x", "axis_y"] {
            let mut p = make_scatter_payload(false);
            p["resolved"]["style"][axis]["labels"]["format"] = serde_json::json!({"spec": ",.0f", "suffix": " CHF"});
            let payload: Payload = serde_json::from_value(p).unwrap();
            let err = render(&payload, &test_fonts()).expect_err("refuses");
            assert!(err.contains(&format!("scatter: {axis}.labels.format")) && err.contains("\"suffix\""), "{err}");
        }
    }

    /// An authored x `ticks.time_unit` is a tickCount interval in production
    /// (`type_inference.py:1026-1052`) that the scatter does not draw.
    #[test]
    fn an_authored_x_tick_cadence_refuses_by_name() {
        let mut p = make_scatter_payload(false);
        p["resolved"]["style"]["axis_x"]["ticks"]["time_unit"] = serde_json::json!("yearmonth");
        let payload: Payload = serde_json::from_value(p).unwrap();
        let err = render(&payload, &test_fonts()).expect_err("refuses");
        assert!(err.contains("scatter: axis_x.ticks.time_unit \"yearmonth\""), "{err}");
    }

    #[test]
    fn a_bare_x_tick_step_refuses_by_name() {
        let mut p = make_scatter_payload(false);
        p["resolved"]["style"]["axis_x"]["ticks"]["step"] = serde_json::json!(5.0);
        let payload: Payload = serde_json::from_value(p).unwrap();
        let err = render(&payload, &test_fonts()).expect_err("refuses");
        assert!(err.contains("scatter: axis_x.ticks.step"), "{err}");
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
        assert!(svg.contains("\u{2026}</text>"), "{svg}");
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
                let text = groups[i].split("</text>").next().unwrap().rsplit('>').next().unwrap();
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
        p["resolved"]["legend"]["values"] = serde_json::json!(["unit_price", "total cost"]);
        let svg = render_json(p);
        let drawn = legend_labels(&svg, &["total cost", "unit price"]);
        assert_eq!(drawn, ["unit price", "total cost"], "{svg}");
        assert_eq!(swatch_fill(&svg, "total cost"), "#3164a3", "{svg}");
        assert_eq!(swatch_fill(&svg, "unit price"), "#779bc9", "{svg}");
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
    fn the_legacy_arm_legend_is_the_shared_legend() {
        let mut p = legacy_unbaked_payload(&[(10.0, 100.0), (20.0, 180.0), (30.0, 220.0)]);
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
}
