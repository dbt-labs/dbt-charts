//! Histogram family (`type: histogram`, resolved `chart_type: "histogram"`
//! — the same `ResolvedBarChart` model as `type: bar`, see
//! `authored/_type.py`: `HISTOGRAM = "histogram"  # -> bar + binning`).
//!
//! Production's own emitter (`emitters/bar.py::_emit_histogram`) does NOT
//! compute bin edges itself: the x encoding is `{"field": chart.x, "bin":
//! true, ...}` — a bare VL `bin: true`, no `maxbins`/`step`/`extent`
//! authored — so Vega-Lite's own `normalizeBin` fills `{maxbins:
//! autoMaxBins("x")}` (10, `vega-lite/src/bin.ts`) and the actual binning
//! (extent over the raw field, then `vega-statistics`'s `bin()` step
//! selection, then `vega-transforms`'s `Bin` per-row assignment) runs
//! inside Vega at render time (via vl-convert), never in Python.
//! `ResolvedBarChart.style.histogram.bin_maxbins` (theme default 20) is
//! genuinely dead: grep confirms no resolve/emit site ever reads it — the
//! emitted spec always says bare `bin: true`.
//!
//! [`vega_bin_step`] ports `vega-statistics::bin()` + `vega-transforms::Bin`'s
//! `_bins` exactly, verified against the vendored `vega`/`vega-lite`
//! sources (not re-derived from Vega-Lite's public docs) — this is the
//! "Python's numbers are the oracle" case turned inside out: there IS no
//! Python number, so the port targets Vega's own published algorithm
//! instead of guessing at one.
//!
//! [`render`] is a single-series fit arm: quantitative binned x (Vega's own
//! `point`-less linear scale over the bin extent), count-aggregate y whose
//! title is `chart.y_label or COUNT_TITLE` (`emitters/bar.py`). The value
//! (y) axis renders on either `"left"` (the theme's plain default) or
//! `"right"`; any other `axis_y.position` refuses by name.
//!
//! The value axis takes one of two shapes, decided exactly as production's
//! `measure_axis_to_vl` decides it. `_emit_histogram` calls it with an EMPTY
//! `y_fields` tuple (the count aggregate has no data column), but it reads
//! `axis.scale.continuous.domain` BEFORE falling through to `numeric_values(
//! data, y_fields)` (`vl_field_maps.py:488-496`):
//! - Own-side `axis_y.labels.align` + a concrete `labels.format` + an authored
//!   numeric domain (no `labels.expr`, `font.case` not upper/lower): the
//!   domain bounds feed `estimated_quantitative_tick_labels`, and the axis
//!   bakes `labelAlign: <orient>` with `labelPadding = widest cover-ladder
//!   label + VALUE_AXIS_BREATHING_ROOM` (`vl_field_maps.py:501-516`). The one
//!   shared `common::estimated_tick_label_gutter` computes it (`bar.rs` and
//!   `line.rs` call the same function). Labels anchor at `tick_size +
//!   labelPadding` growing back toward the plot, so the label ink adds
//!   nothing to the gutter and the rotated title sits `title.padding` past
//!   that anchor. The authored domain feeds ONLY this estimate: production's
//!   y encoding carries no `scale` (`emitters/bar.py::_emit_histogram`), so the
//!   count y-scale never reads it, and the estimate takes `min`/`max` of the
//!   pair, so a descending pair renders its ascending twin.
//! - Anything else (the common case): `_fallback_reversed_label_align` drops
//!   `labelAlign` — Vega-Lite's own un-flipped smart default (labels anchored
//!   away from the plot, `autosize: fit` reserving the REAL drawn-tick ink on
//!   its own). [`render`] mirrors the SAME generic ink-based gutter law on
//!   both sides (widest REAL drawn label width, no breathing room). Confirmed
//!   directly against `ladder/histogram/03-right-axis-refuses`'s own compiled
//!   spec (`axis_y.labels.align` resolves `None`, no `labelAlign` in the
//!   emitted VL axis dict) and its real Vega scenegraph (`vega_to_
//!   scenegraph`): the value-axis label group sits at the plot's right edge,
//!   labels `align: "left"` at local x = `tick_size (0, ticks invisible
//!   here) + labelPadding (6)`, and the rotated title sits past the widest
//!   label's own far edge plus `title.padding`.
//!
//! `axis_y.mirror` repeats the count labels and ticks on the opposite edge
//! (`mirror_axis.py` copies the primary's axis dict): the same label padding,
//! tilt and, for a measured own-side align, the same align.
//!
//! Pinned against a captured production SVG for `charts/more-types_1` (the
//! docs histogram fence) and a self-contained ladder rung
//! (`fixtures/boards/histogram/01-basic-histogram.yml`):
//! - x ticks: `ax_x.ticks.count` DOES reach VL's `tickCount`
//!   (`type_inference.py::apply_x_tick_cadence`, called explicitly for
//!   histogram's x), so `d3rs::d3_ticks(bin_start, bin_stop, count)` over
//!   the RAW bin extent (never re-niced beyond it — the Bin transform's own
//!   nice pass already gives a nice range).
//! - y ticks: `measure_axis_to_vl`/`axis_to_vl` never map `ticks.count` to
//!   VL for a *measure* axis, and `_emit_histogram` sets its own `tickCount`
//!   expression on an axis with no baked ladder (`emitters/bar.py:516-519`):
//!   `max(1, min(ceil(height / 40), domain('y')[1]))`, so the ladder is
//!   `cartesian::unbaked_ticks(0, d3_nice_domain(0, max_count, 10).1,
//!   max(1, min(ceil(plot_h / 40), domain_max)))` — a count-2 domain ticks at
//!   count 2, not 10.
//! - bar geometry: VL's rect positioning for a binned field with a `{band:
//!   f}` mark width (`compile/mark/encode/position-rect.ts::rectBinPosition`)
//!   plus the bar mark's own `binSpacing: 1` config default
//!   (`mark.ts::defaultBarConfig`) and Vega's axis `translate: 0.5` —
//!   empirically confirmed against the captured paths: the bin-END edge
//!   sits at `bin_start_px + (1 − pos)·bin_width_px` (`pos = (1 −
//!   band_width) / 2`) with NO offset, the bin-START edge at `bin_start_px +
//!   pos·bin_width_px + 1.0` (`axisTranslate(0.5) + binSpacing/2(0.5)`,
//!   both Vega-side constants — see [`bar_edges`]).
//! - value-axis gutter (either side, un-flipped default): `tick_eff +
//!   label_pad + widest_label_ink` — NO `VALUE_AXIS_BREATHING_ROOM` (that
//!   term belongs to the own-side measured shape above, which replaces
//!   `label_pad` and drops the ink term; confirmed absent on the left by
//!   probing two cases with different widest labels against their captured
//!   margins, and absent on the right by the `axis_y.mirror`-free scenegraph
//!   probe cited above).
//!   `widest_label_ink` is `measure_shaped` (the ink Vega itself measures
//!   for its own auto-margin, not a width production bakes into the spec —
//!   the font-measurement-policy law). The FREE edge (the side with no
//!   value axis) keeps a minimum `AXIS_FREE_EDGE_PAD` past the plot
//!   regardless of ink (probed on the left-axis case: the flush-anchored
//!   last x label's own ink never reaches it, so this floor is the only
//!   source of the captured 2px); the right-axis case mirrors the same
//!   floor onto the left edge.
//! - both axis groups crisp-shift by +0.5 in every direction
//!   (`docs/laws-shared.md#tickround`): a rule/tick mark rounds its own
//!   local position before this shift (`round(pos) + 0.5`), a label keeps
//!   its exact scale position but still inherits the group's own +0.5 —
//!   the same law `bar.rs`'s `pos = x_pos(tv) + 0.5` and
//!   `y_label_x = plot_w + 0.5 + y_tick_eff + y_gutter` already carry.
//!   Review round 2 caught this arm's own labels and rotated title missing
//!   that shift (`charts/more-types_1`'s 0.0056 CLOSE) — every label
//!   coordinate here now adds it, and the rotated "#" title's
//!   `label_edge_x` (fed to `emit_axis_title_rotated`) is the label
//!   anchor's own INNER edge (`y_label_x`, which now carries the shift)
//!   minus the gutter width, the same `0.5 - pad - tick - gutter` shape
//!   `line.rs`'s `Shape::RailConcat` left-axis title already uses.
//!
//! `chart.layers` is compile-time refused on histogram (`bar.py`'s
//! `_reject_histogram_layers`), so that shape is unreachable and stays
//! unmodeled. `color` is NOT refused at compile time — `BarChart` inherits
//! it unconditionally from `_CartesianChartFields`, and `_emit_histogram`
//! has real color-channel handling. A nominal color stacks each bin's rows by
//! series, ranked by row count (highest first, name breaking ties) with the
//! first series at the base and in palette slot 0; the y domain is the tallest stack's count and the stack's top
//! segment alone takes the corner radius (production clips each bin's stack
//! to one rounded rect). The resolved legend is hidden for a histogram; a
//! visible one, a non-series or quantitative color and a null color value
//! refuse by name.

use std::fmt::Write as _;

use serde::Deserialize;
use serde_json::Value;

use crate::common::{
    axis_title_text, emit_axis_title_bottom,
    emit_axis_title_rotated, format_y_label, row_num, AxisStyle, Font, TitleStyle,
};
use crate::families::legend;
use crate::diagnostic::Diagnostic;
use crate::families::cartesian::{d3_nice_domain, vl_default_tick_count, LinearScale};
use crate::families::color_scale::GradientAt;
use crate::families::color_scale::ContinuousColor;
use crate::families::temporal::{infer_vega_type, VegaType};
use crate::frame::{ChartFrame, FrameTitle};
use crate::svg::{xml_escape, xml_escape_attr, vega_escape_attr};
use crate::text::FontBook;
use crate::vega_fit;
use crate::Payload;

#[derive(Deserialize)]
struct Resolved {
    id: String,
    x: String,
    x_label: Option<String>,
    #[serde(default)]
    y_label: Option<String>,
    field_labels: crate::common::FieldLabels,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    title: Option<String>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    subtitle: Option<String>,
    title_style: Option<TitleStyle>,
    /// Effective chart background — see `BarResolved::background` and
    /// `docs/laws-shared.md#background`.
    background: String,
    /// Legal and reachable on histogram (unlike `chart.layers`) — see the
    /// module doc.
    color: Option<String>,
    /// How the query formats a numeric color column: its gradient legend's labels.
    #[serde(default)]
    color_format: Option<crate::common::FormatSlot>,
    palette: Vec<String>,
    #[serde(default)]
    resolved_channels: std::collections::HashMap<String, crate::common::StyleChannel>,
    legend: crate::families::legend::LegendConfig,
    style: HistogramStyle,
}

#[derive(Deserialize)]
struct HistogramStyle {
    axis_x: AxisStyle,
    axis_y: AxisStyle,
    mark: HistogramMark,
    single_series_fill: String,
    title_font: Option<Font>,
}

#[derive(Deserialize)]
struct HistogramMark {
    border: HistogramBorder,
    band_width: f64,
}

#[derive(Deserialize)]
struct HistogramBorder {
    radius: f64,
    /// A bar's legend swatch carries the mark's border color as its stroke.
    #[serde(default)]
    color: Option<String>,
}

/// Vega's built-in bar-mark constants for a binned field's `{band: f}`
/// width (module doc's "bar geometry" bullet) — never authored, never
/// touched by `bar_mark_to_vl`, so these are the JS defaults themselves,
/// not resolved-payload fields.
const BIN_SPACING: f64 = 1.0;
const AXIS_TRANSLATE: f64 = 0.5;

/// `(left_edge_px, right_edge_px)` for one bin, given its pixel span
/// `[bin_start_px, bin_end_px]` (bin_start_px < bin_end_px) — see the
/// module doc's "bar geometry" bullet.
fn bar_edges(bin_start_px: f64, bin_end_px: f64, band_width: f64) -> (f64, f64) {
    let bin_width_px = bin_end_px - bin_start_px;
    let pos = (1.0 - band_width) / 2.0;
    let right = bin_start_px + (1.0 - pos) * bin_width_px + (AXIS_TRANSLATE - BIN_SPACING / 2.0);
    let left = bin_start_px + pos * bin_width_px + (AXIS_TRANSLATE + BIN_SPACING / 2.0);
    (left, right)
}

/// One stack segment, `(top, bottom)` px at `(x, width)`. A bin's stack is
/// clipped to one rect whose top corners take radius `r` from `stack_top`, so a
/// segment reaching into the curve follows its arc. `None` is a null color's
/// mark, which Vega leaves unfilled under its root group's `fill: none`.
fn emit_stack_segment(
    out: &mut String,
    (x, w): (f64, f64),
    (top, bottom): (f64, f64),
    (stack_top, r): (f64, f64),
    fill: Option<&str>,
    aria: &str,
) {
    let fill = format!(" fill=\"{}\"", xml_escape_attr(fill.unwrap_or("none")));
    let h = bottom - top;
    if r <= 0.0 || top >= stack_top + r {
        let _ = write!(
            out,
            "<rect x=\"{x:.2}\" y=\"{top:.2}\" width=\"{w:.2}\" height=\"{h:.2}\"{fill} stroke-width=\"0\"{aria}/>"
        );
        return;
    }
    // The clip's corner circles are centered `r` below the stack top, `r` in
    // from each side; a segment's side runs along the circle down to the
    // curve's end `stack_top + r` and straight below it.
    let arc_end = bottom.min(stack_top + r);
    let side = |y: f64| r - (r * r - (stack_top + r - y).powi(2)).max(0.0).sqrt();
    let x2 = x + w;
    let (start, down) = if bottom > arc_end {
        (format!("M {x:.2},{bottom:.2} V {arc_end:.2}"), format!(" V {bottom:.2}"))
    } else {
        (format!("M {:.2},{arc_end:.2}", x + side(arc_end)), String::new())
    };
    let d = format!(
        "{start} A {r:.2},{r:.2} 0 0,1 {:.2},{top:.2} H {:.2} A {r:.2},{r:.2} 0 0,1 {:.2},{arc_end:.2}{down} Z",
        x + side(top),
        x2 - side(top),
        x2 - side(arc_end),
    );
    let _ = write!(out, "<path d=\"{d}\"{fill} stroke-width=\"0\"{aria}/>");
}

/// One stack segment's identity: how it paints and what its tooltip row reads.
struct Series {
    /// `None` for a null color, whose mark Vega leaves unfilled.
    fill: Option<String>,
    text: String,
    /// False for a null the invalid filter drops from the marks while its
    /// rows still stack, and so still count toward the bins' height.
    drawn: bool,
}

/// What a visible legend draws.
enum LegendPaint {
    /// One swatch per entry, `(label, fill)` in legend order.
    Symbols(Vec<(String, String)>),
    Gradient(ContinuousColor),
}

/// How a histogram's color channel stacks and paints each bin.
struct StackColor {
    /// The color field's title in a mark's description.
    title: String,
    /// Series in stack order, base first.
    series: Vec<Series>,
    legend: LegendPaint,
}

/// Production's `_emit_histogram` color encoding over `binned_rows` (every
/// row with a numeric x): the stack's series, base first, and each row's
/// series. A row with none is one Vega-Lite's invalid filter drops, which
/// leaves the quantitative and temporal scales' own nulls out of every bin.
///
/// The column types the way `infer_vega_type_from_data` reads it. A nominal
/// one ranks its series by row count and paints the palette in that order. A
/// numeric or date one is a continuous scale over its extent, one segment per
/// distinct value; an ordinal (date-like text) one a `blues` ramp over its
/// sorted values. Vega-Lite stacks those last three in descending value order.
fn stack_color(
    r: &Resolved,
    rows: &[serde_json::Map<String, Value>],
    binned_rows: &[&serde_json::Map<String, Value>],
) -> Result<Option<(StackColor, Vec<Option<usize>>)>, String> {
    let Some(cf) = r.color.as_deref() else { return Ok(None) };
    // `_flag_quantitative_color` stamps every resolved color channel, so a
    // color field without one is a payload no resolver produced.
    let channel = r
        .resolved_channels
        .get("color")
        .ok_or("histogram: a color field carries no resolved color channel (resolver required)")?;
    if !matches!(channel.mode.as_str(), "series" | "gradient") {
        return Err(format!(
            "histogram: a {} color channel is not a histogram payload (resolve produces only series and gradient)",
            channel.mode
        ));
    }
    let title = crate::families::legend::color_channel_title(cf, &r.legend, &r.field_labels)?;
    let continuous = |values: Vec<Option<f64>>,
                      scale: ContinuousColor,
                      text: &dyn Fn(f64) -> Result<String, String>|
     -> Result<(StackColor, Vec<Option<usize>>), String> {
        // A cell that reads as `NaN` is no value: it stacks with the nulls.
        let values: Vec<Option<f64>> = values.into_iter().map(|v| v.filter(|v| v.is_finite())).collect();
        let mut distinct: Vec<f64> = values.iter().flatten().copied().collect();
        distinct.sort_by(|a, b| b.total_cmp(a));
        distinct.dedup();
        let mut series = distinct
            .iter()
            .map(|&v| Ok(Series { fill: Some(scale.fill(v)), text: text(v)?, drawn: scale.paints() }))
            .collect::<Result<Vec<_>, String>>()?;
        // A null sorts below every value, so it stacks last and draws nothing.
        let null_at = series.len();
        if values.iter().any(Option::is_none) {
            series.push(Series { fill: None, text: "null".into(), drawn: false });
        }
        let row_series = values
            .iter()
            .map(|v| match v {
                Some(v) => Some(distinct.iter().position(|&d| d == *v).expect("every kept value is distinct")),
                None => Some(null_at),
            })
            .collect();
        Ok((StackColor { title: title.clone(), series, legend: LegendPaint::Gradient(scale) }, row_series))
    };
    // A numeric color's tooltip prints through the query's format when the
    // visible legend carries it as its own `format`, else a gradient scale's
    // value through d3's default and a plain series' as the bare number.
    let gradient_mode = channel.mode == "gradient";
    let quantitative_text = |v: f64| -> Result<String, String> {
        let spec = match r.color_format.as_ref().filter(|_| r.legend.visible).and_then(crate::families::color_scale::legend_format_spec) {
            Some(spec) => spec,
            None if gradient_mode => "",
            None => return Ok(crate::families::color_scale::js_number_string(v)),
        };
        crate::number_locale::d3_format(spec, v).map_err(|e| format!("histogram color tooltip format: {e}"))
    };
    let extent = |values: &[Option<f64>]| {
        values.iter().flatten().fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), &v| (lo.min(v), hi.max(v)))
    };
    let vega_type = if channel.mode == "gradient" { VegaType::Quantitative } else { infer_vega_type(rows, cf) };
    let color = match vega_type {
        VegaType::Quantitative if channel.mode == "gradient" => {
            let scale = channel.scale.as_ref().ok_or("histogram: a gradient color channel carries no scale (resolver required)")?;
            let values: Vec<Option<f64>> = binned_rows.iter().map(|row| row_num(row, cf)).collect();
            let all = extent(&rows.iter().filter_map(|row| row.get(cf)?.as_f64()).map(Some).collect::<Vec<_>>());
            let all = (all.0 <= all.1).then_some(all);
            continuous(values.clone(), ContinuousColor::gradient(scale, all, extent(&values))?, &quantitative_text)?
        }
        VegaType::Quantitative => {
            let values: Vec<Option<f64>> = binned_rows.iter().map(|row| row_num(row, cf)).collect();
            let (lo, hi) = extent(&values);
            continuous(values, ContinuousColor::blues(lo, hi), &quantitative_text)?
        }
        VegaType::Temporal => {
            let values = binned_rows
                .iter()
                .map(|row| match row.get(cf) {
                    None | Some(Value::Null) => None,
                    Some(cell) => crate::families::heatmap::date_cell_day(cell),
                })
                .collect::<Vec<Option<f64>>>();
            let (lo, hi) = extent(&values);
            continuous(values, ContinuousColor::dates(lo, hi), &crate::families::heatmap::legend_date_label)?
        }
        VegaType::Ordinal => ordinal_color(cf, title, &r.legend, binned_rows),
        VegaType::Nominal => nominal_color(r, cf, title, rows, binned_rows)?,
    };
    Ok(Some(color))
}

/// A nominal color: series ranked by row count, highest first, name breaking
/// ties, counted over every row with a color. The same order puts the first
/// series at each stack's base and hands out palette slots
/// (`spatial_color_scale` without a board scale, so `category_colors` never
/// applies). A null color is a series of its own at the base with no paint
/// (`series_order_expression` ranks it `-1`) that the legend leaves out.
fn nominal_color(
    r: &Resolved,
    cf: &str,
    title: String,
    rows: &[serde_json::Map<String, Value>],
    binned_rows: &[&serde_json::Map<String, Value>],
) -> Result<(StackColor, Vec<Option<usize>>), String> {
    if r.palette.is_empty() {
        return Err("histogram: a color channel needs a non-empty palette".into());
    }
    let mut counts: std::collections::BTreeMap<String, usize> = std::collections::BTreeMap::new();
    for row in rows.iter().filter(|row| row.get(cf).is_some_and(|v| !v.is_null())) {
        *counts.entry(crate::common::row_str(row, cf)).or_default() += 1;
    }
    let mut ranked: Vec<(String, usize)> = counts.into_iter().collect();
    ranked.sort_by(|(a, na), (b, nb)| nb.cmp(na).then_with(|| a.cmp(b)));
    let has_null = binned_rows.iter().any(|row| row.get(cf).is_none_or(Value::is_null));
    let base = usize::from(has_null);
    let fills: Vec<String> = (0..ranked.len()).map(|i| r.palette[i % r.palette.len()].clone()).collect();
    let mut series: Vec<Series> = Vec::new();
    if has_null {
        series.push(Series { fill: None, text: "null".into(), drawn: true });
    }
    series.extend(
        ranked.iter().zip(&fills).map(|((name, _), fill)| Series { fill: Some(fill.clone()), text: name.clone(), drawn: true }),
    );
    let row_series = binned_rows
        .iter()
        .map(|row| {
            if row.get(cf).is_none_or(Value::is_null) {
                return Some(0);
            }
            let value = crate::common::row_str(row, cf);
            Some(base + ranked.iter().position(|(name, _)| *name == value).expect("every colored row is counted"))
        })
        .collect();
    // The legend lists the series top of the stack first.
    let names: Vec<&str> = ranked.iter().rev().map(|(name, _)| name.as_str()).collect();
    let entries = legend_entries(&r.legend, &names, |name| {
        let rank = ranked.iter().position(|(n, _)| n == name).expect("a legend name is a series");
        fills[rank].clone()
    });
    Ok((StackColor { title, series, legend: LegendPaint::Symbols(entries) }, row_series))
}

/// An ordinal (date-like text) color: a `blues` ramp over the sorted values,
/// one segment per value, stacked descending. The ramp samples `blues` at
/// `(i + 1) / (n + 1)` for the `i`-th of `n` values, a null among them as the
/// lowest.
fn ordinal_color(
    cf: &str,
    title: String,
    legend: &crate::families::legend::LegendConfig,
    binned_rows: &[&serde_json::Map<String, Value>],
) -> (StackColor, Vec<Option<usize>>) {
    let value_of = |row: &serde_json::Map<String, Value>| {
        row.get(cf).filter(|v| !v.is_null()).map(|_| crate::common::row_str(row, cf))
    };
    let mut ascending: Vec<Option<String>> = binned_rows.iter().map(|row| value_of(row)).collect();
    ascending.sort();
    ascending.dedup();
    let fill_of = |i: usize| {
        d3rs::color::rgb_text(d3rs::color::quantize_sample(&d3rs::vega_schemes::BLUES, ascending.len(), i))
    };
    let text_of = |v: &Option<String>| v.clone().unwrap_or_else(|| "null".into());
    let series: Vec<Series> = ascending
        .iter()
        .enumerate()
        .rev()
        .map(|(i, v)| Series { fill: Some(fill_of(i)), text: text_of(v), drawn: true })
        .collect();
    let row_series = binned_rows
        .iter()
        .map(|row| {
            let value = value_of(row);
            ascending.iter().position(|v| *v == value).map(|i| ascending.len() - 1 - i)
        })
        .collect();
    let names: Vec<String> = ascending.iter().map(text_of).collect();
    let name_refs: Vec<&str> = names.iter().map(String::as_str).collect();
    let entries = legend_entries(legend, &name_refs, |name| {
        fill_of(names.iter().position(|v| v == name).expect("a legend name is a value"))
    });
    (StackColor { title, series, legend: LegendPaint::Symbols(entries) }, row_series)
}

/// The legend entries a symbol legend lists: `names` in display order, or the
/// authored `legend.values` resolved against them (an empty list draws a
/// title-only legend; one that matches nothing falls back to every name).
fn legend_entries(
    legend: &crate::families::legend::LegendConfig,
    names: &[&str],
    fill: impl Fn(&str) -> String,
) -> Vec<(String, String)> {
    let order: Vec<usize> = match &legend.values {
        Some(authored) if authored.is_empty() => Vec::new(),
        Some(authored) => crate::families::legend::resolve_legend_values(authored, names, None),
        None => (0..names.len()).collect(),
    };
    order.into_iter().map(|i| (names[i].to_string(), fill(names[i]))).collect()
}

pub fn render(payload: &Payload, fonts: &FontBook) -> Result<String, Diagnostic> {
    let r: Resolved = serde_json::from_value(payload.resolved.clone())
        .map_err(|e| format!("histogram: payload.resolved: {e}"))?;
    let ax_x = &r.style.axis_x;
    let ax_y = &r.style.axis_y;
    ax_x.refuse_unpainted_affix("histogram", "axis_x", false)?;
    ax_x.refuse_x_tick_cadence("histogram")?;
    ax_x.refuse_top_x_axis("histogram")?;
    ax_y.refuse_unpainted_affix("histogram", "axis_y", false)?;
    // `_emit_histogram` bakes `orient` from the resolved `axis_y.position`
    // via `measure_axis_to_vl` (`emitters/bar.py:480-484`) — this arm models
    // both left and right; any other position (the theme resolves an
    // unauthored one to the literal string `"left"`) would silently render
    // on the wrong side.
    let val_on_right = match ax_y.position.as_deref() {
        Some("left") => false,
        Some("right") => true,
        Some(other) => return Err(format!("histogram: axis_y.position {other:?} is neither left nor right").into()),
        None => return Err("histogram: axis_y.position absent (theme required)".into()),
    };
    // `axis_y.mirror: true` repeats the labels and ticks on the opposite edge at
    // the primary's own offset (`mirror_axis.py`); a ghost `format` refuses.
    let mirror_active = ax_y.mirror_ghost("histogram", false)?.is_some();
    // `measure_axis_to_vl` bakes a measured `labelPadding` only for an
    // own-side `labelAlign` (`vl_field_maps.py:457-461`) on an axis whose
    // tick labels it can reconstruct: no `labelExpr` (an authored
    // `labels.expr`), no upper/lower `font.case`, a concrete `labels.format`
    // (`:478-486`), and — since histogram's `tick_values` and `y_fields` are
    // both empty — an authored numeric `scale.continuous.domain` to estimate
    // them from (`:488-496`). Anything less falls back to Vega-Lite's
    // un-flipped default (`_fallback_reversed_label_align`, `:518`).
    let own_side = if val_on_right { "right" } else { "left" };
    let own_side_measured = ax_y.labels.align.as_deref() == Some(own_side)
        && ax_y.labels.format.is_some()
        && ax_y.labels.expr.is_none()
        && !matches!(ax_y.labels.font.case.as_deref(), Some("upper") | Some("lower"))
        && ax_y.authored_continuous_domain().is_some();
    // The count y-scale below is always `[0, d3_nice(0, max_count, 10).1]`
    // (`y_domain_max`): production's y encoding carries no `scale`
    // (`emitters/bar.py::_emit_histogram`), so an authored pair reaches Vega
    // only through the own-side-align gutter estimate above, which takes
    // `min`/`max` of the pair (`common::estimated_tick_label_gutter`), so
    // its order is irrelevant.

    let binned_rows: Vec<&serde_json::Map<String, Value>> =
        payload.rows.iter().filter(|row| row_num(row, &r.x).is_some()).collect();
    let (stack_color, row_series) = match stack_color(&r, &payload.rows, &binned_rows)? {
        Some((color, row_series)) => (Some(color), row_series),
        None => (None, vec![Some(0); binned_rows.len()]),
    };
    let series_count = stack_color.as_ref().map_or(1, |c| c.series.len());
    let binned: Vec<(f64, Option<usize>)> = binned_rows
        .iter()
        .zip(&row_series)
        .map(|(row, series)| (row_num(row, &r.x).expect("filtered to numeric x"), *series))
        .collect();
    let bins = histogram_bins_by(&binned, series_count.max(1))?;
    let bin_start = bins.first().expect("histogram_bins never returns empty").start;
    let bin_stop = bins.last().expect("histogram_bins never returns empty").end;
    if bin_stop <= bin_start {
        return Err("histogram: x domain is degenerate (max ≤ min)".into());
    }
    // Vega-Lite's invalid filter can leave no mark at all, which production
    // raises as a chart that painted nothing.
    let paints_a_mark = bins.iter().any(|bin| {
        bin.counts.iter().enumerate().any(|(i, &n)| n > 0 && stack_color.as_ref().is_none_or(|c| c.series[i].drawn))
    });
    if !paints_a_mark {
        return Err(payload.painted_no_marks());
    }
    let max_count = bins.iter().map(Bin::count).max().unwrap_or(0) as f64;
    if max_count <= 0.0 {
        return Err("histogram: every bin is empty — nothing to draw".into());
    }

    let svg_w = payload.width;
    let svg_h = payload.height;
    let pad = payload.padding.as_ref();
    let pad_l = pad.map(|p| p.left).unwrap_or(0.0);
    let pad_r = pad.map(|p| p.right).unwrap_or(0.0);
    let pad_t = pad.map(|p| p.top).unwrap_or(0.0);
    let pad_b = pad.map(|p| p.bottom).unwrap_or(0.0);
    // The chart's OWN effective background — see `Resolved::background`.
    let bg = r.background.as_str();

    // ── X: a binned scale ticks every bin edge. Vega's `tickCount` lifts any
    // count to `scale.bins.length` (`vega-scale` `tickCount`), so the
    // authored or themed `ticks.count` thins nothing.
    let x_fmt = ax_x
        .labels
        .spec()
        .ok_or("histogram: axis_x.labels.format missing (theme required)")?;
    let x_ticks_domain: Vec<f64> = bins.iter().map(|bin| bin.start).chain([bin_stop]).collect();
    let x_tick_labels: Vec<String> = x_ticks_domain
        .iter()
        .map(|&tv| crate::number_locale::d3_format(x_fmt, tv).map_err(|e| format!("histogram x label format: {e}")))
        .collect::<Result<_, _>>()?;

    // ── Y: never baked (count aggregate has no static column) — the
    // "unbaked, uncounted value axis" law (module doc), re-derived at the
    // fitted extent below via `unbaked_y_ticks`.
    let y_domain_max = d3_nice_domain(0.0, max_count, 10).1;
    let y_fmt = ax_y.labels.format.as_ref();
    let unbaked_y_ticks = |plot_h: f64| -> Vec<f64> {
        // `_emit_histogram`'s `tickCount` expression, `max(1, min(ceil(height /
        // 40), domain('y')[1]))` (`bar.py:516-519`): never more ticks than the
        // domain's top count. A nice domain over whole counts tops out whole.
        let count = (vl_default_tick_count(plot_h) as f64)
            .min(y_domain_max)
            .max(1.0) as usize;
        crate::families::cartesian::unbaked_ticks(ax_y, 0.0, y_domain_max, count)
    };

    let title = FrameTitle::new(
        r.title.as_deref(),
        r.subtitle.as_deref(),
        r.style.title_font.as_ref(),
        r.title_style.as_ref(),
        svg_w - pad_l - pad_r,
        fonts,
    )?;
    title.warn_truncated(&r.id);

    // A symbol legend's box does not depend on the plot; a gradient's follows
    // the plot's height and is laid out per fit pass.
    let legend_title = match r.color.as_deref() {
        Some(cf) if stack_color.is_some() => legend::legend_title_text(cf, &r.legend, &r.field_labels)?,
        _ => String::new(),
    };
    let swatch = legend::LegendSymbol::Square(r.style.mark.border.color.as_deref().unwrap_or("none"));
    let symbol_entries: Vec<legend::LegendEntry> = match &stack_color {
        Some(StackColor { legend: LegendPaint::Symbols(entries), .. }) if r.legend.visible => entries
            .iter()
            .map(|(label, fill)| legend::LegendEntry {
                label,
                fill,
                symbol: Some(swatch),
                series_key: None,
            })
            .collect(),
        _ => Vec::new(),
    };
    let symbol_legend = (r.legend.visible
        && matches!(&stack_color, Some(StackColor { legend: LegendPaint::Symbols(_), .. }))
        && !(symbol_entries.is_empty() && legend_title.is_empty()))
    .then(|| legend::vega_legend(&symbol_entries, false, &legend_title, &r.legend, fonts))
    .transpose()?;
    let gradient_color = match &stack_color {
        Some(StackColor { legend: LegendPaint::Gradient(color), .. }) if r.legend.visible => Some(color),
        _ => None,
    };
    let gradient_id = format!("hist_grad-{}", crate::contract::safe_svg_id(&r.id));
    // The legend's origin in plot coordinates, set on every fit pass (the last
    // is the fitted layout).
    let legend_xy = std::cell::Cell::new((0.0, 0.0));

    let x_font_size = ax_x.labels.font.size_or(11.0);
    let x_family = ax_x.labels.font.family_or("sans-serif");
    let x_label_pad = ax_x.labels.padding.unwrap_or(6.0);
    let x_axis_offset = ax_x.ticks.offset.unwrap_or(0.0);
    let x_ticks_visible = ax_x.ticks.visible.unwrap_or(true);
    let x_tick_len = ax_x.ticks.length.unwrap_or(6.0);
    let x_tick_eff = if x_ticks_visible { x_tick_len } else { 0.0 };
    let x_limit = ax_x.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    let x_label_ws: Vec<f64> = x_tick_labels
        .iter()
        .map(|lbl| fonts.measure(lbl, x_family, x_font_size))
        .collect::<Result<_, _>>()?;

    let y_font_size = ax_y.labels.font.size_or(11.0);
    let y_family = ax_y.labels.font.family_or("sans-serif");
    let y_label_pad = ax_y.labels.padding.unwrap_or(6.0);
    let y_ticks_visible = ax_y.ticks.visible.unwrap_or(true);
    let y_tick_len = ax_y.ticks.length.unwrap_or(6.0);
    let y_tick_eff = if y_ticks_visible { y_tick_len } else { 0.0 };
    let y_limit = ax_y.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    // The measured shape replaces the themed `labelPadding` with the
    // cover-ladder estimate over the authored domain (`vl_field_maps.py:
    // 498-516`) — the same law `bar.rs` and `line.rs` apply, one function.
    let measured_label_pad = if own_side_measured {
        Some(crate::common::estimated_tick_label_gutter(
            fonts,
            y_family,
            y_font_size,
            y_limit,
            ax_y.tick_label.as_ref(),
            y_fmt,
            ax_y,
            0.0,
            max_count,
        )?)
    } else {
        None
    };
    // Vega anchors a label `tick + labelPadding` from the axis line. The
    // default alignment grows its ink away from the plot, so the gutter adds
    // the widest label; the own-side one grows back toward the plot, so the
    // anchor is the gutter's far edge and the ink adds nothing.
    let y_label_gap = measured_label_pad.unwrap_or(y_label_pad);
    let y_labels_flipped = measured_label_pad.is_some();
    let y_labels_start = val_on_right != y_labels_flipped;
    let y_angle = ax_y.labels.angle.unwrap_or(0.0);
    // Vega-Lite's default label anchor for a rotated y axis; a measured own-side
    // `labelAlign` is authored, so only the baseline follows the angle.
    let y_angle_norm = vega_fit::normalize_angle(y_angle);
    let (vl_align, y_baseline) = vega_fit::vl_y_label_anchor(y_angle_norm, !val_on_right);
    let y_align = if y_labels_flipped || y_angle == 0.0 {
        if y_labels_start { "left" } else { "right" }
    } else {
        vl_align
    };
    // The ghost keeps a measured own-side `labelAlign` verbatim (it is authored,
    // `mirror_axis.py` copies the primary's axis); otherwise it takes
    // Vega-Lite's default for its own edge.
    let (ghost_default_align, ghost_baseline) = vega_fit::vl_y_label_anchor(y_angle_norm, val_on_right);
    let ghost_align = if y_labels_flipped { y_align } else { ghost_default_align };
    // How far the labels' ink reaches past their anchor, away from the plot
    // (`s = -bounds.x1` for a left axis): the widest label, or the widest
    // rotated ink about each label's own anchor.
    let y_ink_gutter = |widths: &[f64], ticks: &[f64], ph: f64| -> f64 {
        if y_labels_flipped {
            return 0.0;
        }
        if y_angle == 0.0 {
            return widths.iter().cloned().fold(0.0_f64, f64::max).min(y_limit);
        }
        widths
            .iter()
            .zip(ticks)
            .map(|(&w, &tv)| {
                let y = ph * (1.0 - tv / y_domain_max);
                let ink = vega_fit::text_ink(0.0, y, w.min(y_limit), y_font_size, y_align, y_baseline).rotate(y_angle, 0.0, y);
                if val_on_right { ink.x2 } else { -ink.x1 }
            })
            .fold(0.0_f64, f64::max)
    };

    let x_title_text = axis_title_text(&r.x, r.x_label.as_deref(), &r.field_labels)?;
    // `emitters/bar.py`: `chart.y_label or COUNT_TITLE` — an authored
    // `y_label` (or an empty-string one, Python's `or` is falsy-checked)
    // overrides the literal "#" fallback every histogram's y aggregate
    // otherwise carries.
    let y_title_text = r
        .y_label
        .as_deref()
        .filter(|s| !s.is_empty())
        .map_or_else(|| "#".to_string(), crate::common::collapse_title_newlines);
    let x_title_paint =
        crate::common::wrapped_axis_title(&r.id, "x_label", &x_title_text, &ax_x.title.font, svg_w, fonts)?;
    let y_title_paint = y_title_text.clone();
    let y_title_extra = crate::common::y_title_extra(&ax_y.title, &y_title_paint);
    let x_title_extra = crate::common::x_title_extra(&ax_x.title, &x_title_paint);
    let x_title_w = crate::common::widest_title_line(&ax_x.title, &x_title_paint, fonts)?;
    let y_title_w = crate::common::widest_title_line(&ax_y.title, &y_title_paint, fonts)?;

    let stroke_color = "none";
    let _ = stroke_color;
    let corner_r = r.style.mark.border.radius;
    let band_width = r.style.mark.band_width;

    // ── Fit ──────────────────────────────────────────────────────────────
    let mut fit_err: Option<String> = None;
    let m = vega_fit::fit_plot(
        svg_w,
        svg_h,
        (pad_l, pad_r, pad_t, pad_b),
        |pw, ph, b| {
            if fit_err.is_some() {
                return;
            }
            let ink = |b: &mut vega_fit::Bounds| -> Result<(), String> {
                let y_ticks = unbaked_y_ticks(ph);
                let y_labels: Vec<String> = y_ticks
                    .iter()
                    .enumerate()
                    .map(|(i, &tv)| format_y_label(tv, None, None, y_fmt, i, y_ticks.len()))
                    .collect::<Result<_, _>>()?;
                let y_label_ws: Vec<f64> = y_labels
                    .iter()
                    .map(|lbl| fonts.measure_shaped(lbl, y_family, y_font_size))
                    .collect::<Result<_, _>>()?;
                let y_gutter = y_ink_gutter(&y_label_ws, &y_ticks, ph);
                // The FREE edge (no value axis) still keeps a minimum
                // breathing room past the plot (`AXIS_FREE_EDGE_PAD`, same
                // small reservation `bar.rs`'s own axis-free edge uses) —
                // probed: the reference's free-edge margin is 2px past an
                // edge-flush label whose own ink stays inside the plot, so
                // this floor is the only source of it. The gutter side
                // mirrors around `pw` when the value axis is on the right —
                // module doc's own law.
                let gutter_reserve = y_tick_eff + y_label_gap + y_gutter + y_title_extra;
                if val_on_right {
                    b.union(&vega_fit::Bounds::new(
                        -crate::common::AXIS_FREE_EDGE_PAD,
                        0.0,
                        pw + gutter_reserve,
                        ph,
                    ));
                } else {
                    b.union(&vega_fit::Bounds::new(
                        -gutter_reserve,
                        0.0,
                        pw + crate::common::AXIS_FREE_EDGE_PAD,
                        ph,
                    ));
                }
                if y_title_extra > 0.0 {
                    let x = if val_on_right { pw + gutter_reserve } else { -gutter_reserve };
                    b.union(&vega_fit::Bounds::new(x, ph / 2.0 - y_title_w / 2.0, x, ph / 2.0 + y_title_w / 2.0));
                }
                if mirror_active {
                    let x = if val_on_right { -(y_tick_eff + y_label_gap) } else { pw + y_tick_eff + y_label_gap };
                    for (&lw, &tv) in y_label_ws.iter().zip(&y_ticks) {
                        let y = ph * (1.0 - tv / y_domain_max);
                        let ink = vega_fit::text_ink(x, y, lw.min(y_limit), y_font_size, ghost_align, ghost_baseline);
                        b.union(&if y_angle == 0.0 { ink } else { ink.rotate(y_angle, x, y) });
                    }
                }
                for (&lw, &tv) in y_label_ws.iter().zip(&y_ticks) {
                    let x = if val_on_right {
                        pw + y_tick_eff + y_label_gap
                    } else {
                        -(y_tick_eff + y_label_gap)
                    };
                    if y_angle == 0.0 {
                        b.union(&vega_fit::text_ink(x, 0.0, lw.min(y_limit), y_font_size, y_align, "middle"));
                    } else {
                        let y = ph * (1.0 - tv / y_domain_max);
                        let ink = vega_fit::text_ink(x, y, lw.min(y_limit), y_font_size, y_align, y_baseline);
                        b.union(&ink.rotate(y_angle, x, y));
                    }
                }
                let row_y = ph + x_axis_offset + x_tick_eff + x_label_pad;
                let pass_x_scale = LinearScale::new(bin_start, bin_stop, 0.0, pw);
                for (&tv, &lw) in x_ticks_domain.iter().zip(x_label_ws.iter()) {
                    let pos = pass_x_scale.position(tv);
                    let align = if pos <= 1.0 {
                        "left"
                    } else if pos >= pw - 1.0 {
                        "right"
                    } else {
                        "center"
                    };
                    b.union(&vega_fit::text_ink(pos, row_y, lw.min(x_limit), x_font_size, align, "top"));
                }
                let mut x_axis_y2 = b.y2.max(ph);
                if x_title_extra > 0.0 {
                    let ext = (b.y2 - ph).max(0.0);
                    x_axis_y2 = ph + ext + x_title_extra;
                    b.union(&vega_fit::Bounds::new(pw / 2.0 - x_title_w / 2.0, 0.0, pw / 2.0 + x_title_w / 2.0, x_axis_y2));
                }
                // The legend anchors on Vega's `xBounds`/`yBounds`: the plot
                // rect and its axes of that orientation.
                let legend_size = match (&symbol_legend, gradient_color) {
                    (Some(lg), _) => Some((lg.box_w, lg.box_h())),
                    (None, Some(color)) => {
                        let at = GradientAt::new(&r.legend, r.color_format.as_ref(), color, (pw, ph))?;
                        let legend = at.legend(&r.legend, color, &gradient_id, &legend_title, fonts)?;
                        Some((legend.box_width(at.length), legend.height(at.length)))
                    }
                    (None, None) => None,
                };
                if let Some(size) = legend_size {
                    let mut x_bounds = if val_on_right { (0.0, pw + gutter_reserve) } else { (-gutter_reserve, pw) };
                    if mirror_active {
                        let far = if val_on_right { -(y_tick_eff + y_label_gap) } else { pw + y_tick_eff + y_label_gap };
                        let reach = y_label_ws.iter().fold(0.0_f64, |w, &lw| w.max(lw.min(y_limit)));
                        x_bounds = if val_on_right { (far - reach, x_bounds.1) } else { (x_bounds.0, far + reach) };
                    }
                    let origin = legend::legend_origin(&r.legend.position, x_bounds, (0.0, x_axis_y2), (pw, ph), size);
                    legend_xy.set(origin);
                    if let Some(reach) = legend::legend_fit_bounds(&r.legend.position, origin, size) {
                        b.union(&reach);
                    }
                }
                // Bar rect ink (top corner radius only — never exceeds the
                // straight rect bounds).
                let y0 = ph;
                for bin in &bins {
                    let x_scale = LinearScale::new(bin_start, bin_stop, 0.0, pw);
                    let (left, right) = bar_edges(x_scale.position(bin.start), x_scale.position(bin.end), band_width);
                    let top = ph * (1.0 - bin.count() as f64 / y_domain_max);
                    b.union(&vega_fit::Bounds::new(left, top.min(y0), right, top.max(y0)));
                }
                Ok(())
            };
            if let Err(e) = ink(b) {
                fit_err = Some(e);
            }
        },
        title.ink(),
    );
    if let Some(e) = fit_err {
        return Err(e.into());
    }
    if m.plot_w <= 0.0 || m.plot_h <= 0.0 {
        return Err(format!("histogram: plot box too small ({}×{})", m.plot_w, m.plot_h).into());
    }
    let (plot_w, plot_h) = (m.plot_w, m.plot_h);
    let x_scale = LinearScale::new(bin_start, bin_stop, 0.0, plot_w);
    let y_ticks = unbaked_y_ticks(plot_h);
    let y_labels: Vec<String> = y_ticks
        .iter()
        .enumerate()
        .map(|(i, &tv)| format_y_label(tv, None, None, y_fmt, i, y_ticks.len()))
        .collect::<Result<_, _>>()?;
    let y_label_ws: Vec<f64> = y_labels
        .iter()
        .map(|lbl| fonts.measure_shaped(lbl, y_family, y_font_size))
        .collect::<Result<_, _>>()?;
    let y_gutter = y_ink_gutter(&y_label_ws, &y_ticks, plot_h);
    let y_pos = |count: f64| plot_h * (1.0 - count / y_domain_max);

    // ── Emission ─────────────────────────────────────────────────────────
    let mut svg = String::with_capacity(16384);
    ChartFrame::new(svg_w, svg_h, (pad_l, pad_r, pad_t, pad_b), bg, crate::svg::num_full).open(&mut svg);
    title.paint(&mut svg, m.title_pos, (pad_l + m.left, pad_t + m.top));
    let group_x = pad_l + m.left;
    let group_y = pad_t + m.top;
    let _ = write!(svg, "<g transform=\"translate({group_x:.3},{group_y:.3})\">");

    if ax_y.grid.visible.unwrap_or(false) {
        let gc = crate::common::required_ink(ax_y.grid.color.as_deref(), "histogram", "axis_y.grid.color")?;
        let gw = ax_y.grid.width.unwrap_or(1.0);
        let go = ax_y.grid.opacity.unwrap_or(1.0);
        for &tv in &y_ticks {
            let gy = y_pos(tv).round() + 0.5;
            let _ = write!(
                svg,
                "<line x1=\"0.5\" y1=\"{gy:.3}\" x2=\"{:.3}\" y2=\"{gy:.3}\" \
                 stroke=\"{gc}\" stroke-width=\"{gw}\" opacity=\"{go}\"/>",
                plot_w + 0.5, gc = xml_escape_attr(gc)
            );
        }
    }

    // Bars. An empty bin (count == 0) paints nothing — Vega's own
    // `aggregate: count` groups the binned field, so a bin no ROW ever
    // lands in produces no group and no mark, never a zero-height rect.
    // Vega-Lite's default description of a bar: every encoded channel as
    // `title: value`, the bin as its formatted range, both through the axes'
    // own label formats.
    let y_fmt_spec = ax_y.labels.spec();
    crate::contract::mark_group_open(&mut svg, "rect", "marks")?;
    for bin in bins.iter().filter(|b| b.count() > 0) {
        let (left, right) = bar_edges(x_scale.position(bin.start), x_scale.position(bin.end), band_width);
        let fmt = |spec: &str, v: f64| {
            crate::number_locale::d3_format(spec, v).map_err(|e| format!("histogram tooltip format: {e}"))
        };
        let span = format!("{} \u{2013} {}", fmt(x_fmt, bin.start)?, fmt(x_fmt, bin.end)?);
        // One rounded clip over the whole stack: it rounds the stack's top
        // corners, and a segment inside the curve follows it.
        let stack_h = plot_h - y_pos(bin.count() as f64);
        let corner = corner_r.min((right - left) / 2.0).min(stack_h / 2.0);
        let stack_top = y_pos(bin.count() as f64);
        let mut below = 0;
        for (series_idx, &n) in bin.counts.iter().enumerate().filter(|(_, n)| **n > 0) {
            let bottom = y_pos(below as f64);
            below += n;
            let top = y_pos(below as f64);
            // A count axis with no label format prints the bare number.
            let count = match y_fmt_spec {
                Some(spec) => fmt(spec, n as f64)?,
                None => n.to_string(),
            };
            let mut rows = vec![(x_title_text.as_str(), span.clone()), (y_title_text.as_str(), count)];
            let series = stack_color.as_ref().map(|c| &c.series[series_idx]);
            if series.is_some_and(|s| !s.drawn) {
                continue;
            }
            if let (Some(color), Some(series)) = (&stack_color, series) {
                rows.push((color.title.as_str(), series.text.clone()));
            }
            let label = crate::contract::native_label(&rows);
            let aria = crate::contract::aria_attr(Some(&label), crate::contract::ItemRole::Bar)?;
            let fill = match series {
                Some(series) => series.fill.as_deref(),
                None => Some(r.style.single_series_fill.as_str()),
            };
            emit_stack_segment(&mut svg, (left, right - left), (top, bottom), (stack_top, corner), fill, &aria);
        }
    }
    crate::contract::mark_group_close(&mut svg);

    let (lx, ly) = legend_xy.get();
    if let Some(lg) = &symbol_legend {
        let (dx, dy) = legend::legend_slide(&r.id, &r.legend.position, lg, (plot_w, plot_h))?;
        legend::emit_vega_legend(&mut svg, &symbol_entries, lg, &r.legend, swatch, lx + dx, ly + dy, false);
    } else if let Some(color) = gradient_color {
        let at = GradientAt::new(&r.legend, r.color_format.as_ref(), color, (plot_w, plot_h))?;
        let legend = at.legend(&r.legend, color, &gradient_id, &legend_title, fonts)?;
        let block = (legend.box_width(at.length), legend.height(at.length));
        let (dx, dy) = legend::slide_block(&r.id, &r.legend.position, block, (plot_w, plot_h))?;
        legend.paint(&mut svg, lx + dx, ly + dy, at.length);
    }

    // X ticks + labels.
    let x_axis_drop = x_axis_offset + 0.5;
    crate::contract::axis_group_open(&mut svg, crate::contract::AxisChannel::X, (0.5, plot_h + x_axis_drop))?;
    if x_ticks_visible {
        let tc = crate::common::required_ink(ax_x.ticks.color.as_deref(), "histogram", "axis_x.ticks.color")?;
        let tw = ax_x.ticks.width.unwrap_or(1.0);
        for &tv in &x_ticks_domain {
            let gx = x_scale.position(tv).round() + 0.5;
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
    let x_label_y =
        plot_h + x_axis_drop + x_tick_eff + x_label_pad + vega_fit::baseline_shift("top", x_font_size);
    for (&tv, lbl) in x_ticks_domain.iter().zip(x_tick_labels.iter()) {
        let pos = x_scale.position(tv);
        // labelOverlap: allow (module doc) — every tick always draws; the
        // edges still flush-anchor (VL's default labelFlush on a
        // continuous scale).
        let anchor = if pos <= 1.0 {
            "start"
        } else if pos >= plot_w - 1.0 {
            "end"
        } else {
            "middle"
        };
        // Vega's x-axis group crisp-shifts by +0.5 in both directions
        // (`tickRound`, `docs/laws-shared.md#tickround`); `x_label_y` above
        // already carries it on the constant axis-perpendicular coordinate,
        // this is the same term on the label's own varying position.
        let label_x = pos + 0.5;
        let _ = write!(
            svg,
            "<text x=\"{label_x:.3}\" y=\"{x_label_y:.3}\" text-anchor=\"{anchor}\" \
             font-family=\"{}\" font-size=\"{x_font_size}\" font-weight=\"{x_label_weight}\" \
             fill=\"{x_label_color}\">{}</text>",
            vega_escape_attr(x_family),
            xml_escape(lbl), anchor = xml_escape_attr(anchor), x_label_weight = xml_escape_attr(&x_label_weight), x_label_color = xml_escape_attr(x_label_color)
        );
    }

    emit_axis_title_bottom(
        &mut svg,
        &x_title_paint,
        &ax_x.title,
        plot_w / 2.0,
        x_label_y,
        x_font_size,
        Some(crate::contract::LabelKey::X),
    );
    crate::contract::axis_group_close(&mut svg);

    // Y ticks + labels — left or right, per `val_on_right` (module doc: a
    // right-side value axis mirrors the left one exactly, no breathing room).
    crate::contract::axis_group_open(&mut svg, crate::contract::AxisChannel::Y, (if val_on_right { plot_w + 0.5 } else { 0.5 }, 0.0))?;
    if y_tick_eff > 0.0 {
        let tc = crate::common::required_ink(ax_y.ticks.color.as_deref(), "histogram", "axis_y.ticks.color")?;
        let tw = ax_y.ticks.width.unwrap_or(1.0);
        let (tick_x1, tick_x2) =
            if val_on_right { (plot_w, plot_w + y_tick_eff) } else { (-y_tick_eff, 0.0) };
        for &tv in &y_ticks {
            let gy = y_pos(tv).round() + 0.5;
            let _ = write!(
                svg,
                "<line x1=\"{tick_x1:.3}\" y1=\"{gy:.3}\" x2=\"{tick_x2:.3}\" y2=\"{gy:.3}\" \
                 stroke=\"{tc}\" stroke-width=\"{tw}\"/>", tc = xml_escape_attr(tc)
            );
        }
    }
    let y_label_color = ax_y.labels.font.color.as_deref().unwrap_or("#626366");
    let y_label_weight = ax_y.labels.font.weight_str();
    // Same crisp-shift as the x-axis labels above, on the y-axis group's own
    // pair of coordinates: +0.5 on both the constant (away-from-plot) edge
    // and each tick's own varying position.
    let y_label_x = if val_on_right {
        plot_w + y_tick_eff + y_label_gap + 0.5
    } else {
        -(y_tick_eff + y_label_gap) + 0.5
    };
    let paint_y_label = |svg: &mut String, x: f64, (align, baseline): (&str, &str), tv: f64, lbl: &str| {
        let anchor = match align {
            "left" => "start",
            "right" => "end",
            _ => "middle",
        };
        let shift = vega_fit::baseline_shift(baseline, y_font_size);
        let placement = if y_angle != 0.0 {
            format!("text-anchor=\"{anchor}\" transform=\"translate({x:.3},{:.3}) rotate({y_angle}) translate(0,{shift})\"", y_pos(tv) + 0.5)
        } else {
            format!("x=\"{x:.3}\" y=\"{:.3}\" text-anchor=\"{anchor}\"", y_pos(tv) + 0.5 + shift)
        };
        let _ = write!(
            svg,
            "<text {placement} font-family=\"{}\" font-size=\"{y_font_size}\" \
             font-weight=\"{y_label_weight}\" fill=\"{y_label_color}\">{}</text>",
            vega_escape_attr(y_family),
            xml_escape(lbl), y_label_weight = xml_escape_attr(&y_label_weight), y_label_color = xml_escape_attr(y_label_color)
        );
    };
    for (&tv, lbl) in y_ticks.iter().zip(y_labels.iter()) {
        paint_y_label(&mut svg, y_label_x, (y_align, if y_angle == 0.0 { "middle" } else { y_baseline }), tv, lbl);
    }

    // `label_edge_x` is the gutter's OUTER edge: `y_label_x`'s own INNER
    // edge (the label anchor, above) plus/minus the gutter width, carrying
    // the same +0.5 crisp shift. Under the measured own-side shape the
    // gutter is 0, so the outer edge is the anchor itself (tick plus
    // `labelPadding`). Line's rail-concat left-axis title uses this identical
    // `0.5 - pad - tick - gutter` shape (`line.rs`'s `Shape::RailConcat`
    // arm); the right-axis case mirrors it around `plot_w` with `sign: 1.0`.
    let (label_edge_x, sign) = if val_on_right {
        (plot_w + 0.5 + y_tick_eff + y_label_gap + y_gutter, 1.0)
    } else {
        (0.5 - y_tick_eff - y_label_gap - y_gutter, -1.0)
    };
    emit_axis_title_rotated(
        &mut svg,
        &y_title_paint,
        &ax_y.title,
        label_edge_x,
        plot_h / 2.0,
        sign,
        Some(crate::contract::LabelKey::Y),
    );
    crate::contract::axis_group_close(&mut svg);
    if mirror_active {
        crate::contract::axis_group_open(
            &mut svg,
            crate::contract::AxisChannel::Y,
            (if val_on_right { 0.5 } else { plot_w + 0.5 }, 0.0),
        )?;
        if y_tick_eff > 0.0 {
            let tc = crate::common::required_ink(ax_y.ticks.color.as_deref(), "histogram", "axis_y.ticks.color")?;
            let tw = ax_y.ticks.width.unwrap_or(1.0);
            let (tick_x1, tick_x2) =
                if val_on_right { (-y_tick_eff, 0.0) } else { (plot_w, plot_w + y_tick_eff) };
            for &tv in &y_ticks {
                let gy = y_pos(tv).round() + 0.5;
                let _ = write!(
                    svg,
                    "<line x1=\"{tick_x1:.3}\" y1=\"{gy:.3}\" x2=\"{tick_x2:.3}\" y2=\"{gy:.3}\" \
                     stroke=\"{tc}\" stroke-width=\"{tw}\"/>",
                    tc = xml_escape_attr(tc)
                );
            }
        }
        let ghost_x = if val_on_right { -(y_tick_eff + y_label_gap) + 0.5 } else { plot_w + y_tick_eff + y_label_gap + 0.5 };
        for (&tv, lbl) in y_ticks.iter().zip(y_labels.iter()) {
            paint_y_label(&mut svg, ghost_x, (ghost_align, ghost_baseline), tv, lbl);
        }
        crate::contract::axis_group_close(&mut svg);
    }

    svg.push_str("</g></svg>");
    Ok(svg)
}

/// One Vega `bin()` computation: `start`/`stop`/`step` for a field's
/// `[min, max]` extent. Ports `vega-statistics/src/bin.js` verbatim for the
/// params VL's `bin: true` actually passes (`base: 10`, `divide: [5, 2]`,
/// `nice: true`, no `step`/`steps`/`minstep`/`anchor` authored) — the
/// `steps`/explicit-`step` branches are dead for this caller and refused by
/// name rather than carried as unreachable code.
fn vega_bin_step(min: f64, max: f64, maxbins: f64) -> (f64, f64, f64) {
    let base = 10.0_f64;
    let logb = base.ln();
    let divide = [5.0_f64, 2.0];
    // `_.span || (max - min) || Math.abs(min) || 1` — JS falsy-chain: fall
    // through only when the previous term is exactly 0.
    let span = if max - min != 0.0 {
        max - min
    } else if min != 0.0 {
        min.abs()
    } else {
        1.0
    };

    let level = (maxbins.ln() / logb).ceil();
    let mut step = base.powf((span.ln() / logb).round() - level).max(0.0);
    while (span / step).ceil() > maxbins {
        step *= base;
    }
    for d in divide {
        let v = step / d;
        if v >= 0.0 && span / v <= maxbins {
            step = v;
        }
    }

    // nice: true (VL's default, and `bin: true` never sets it false) —
    // floor `min` to a step multiple (with the same float-error epsilon
    // bias vega-statistics uses) and ceil `max`.
    let precision = if step.ln() >= 0.0 {
        0.0
    } else {
        (-step.ln() / logb).trunc() + 1.0
    };
    let eps = base.powf(-precision - 1.0);
    let niced_min_candidate = (min / step + eps).floor() * step;
    let niced_min = if min < niced_min_candidate {
        niced_min_candidate - step
    } else {
        niced_min_candidate
    };
    let niced_max = (max / step).ceil() * step;

    let start = niced_min;
    let stop = if niced_max == niced_min {
        niced_min + step
    } else {
        niced_max
    };
    (start, stop, step)
}

/// A single bin: `[start, end)`, half-open except the last bin, which is
/// closed (matches Vega's own `v > stop ? +Infinity` / clamp-to-`stop-step`
/// assignment — the last bin absorbs a value exactly at `stop`).
pub struct Bin {
    pub start: f64,
    pub end: f64,
    /// Rows per series, in series order (one entry without a color channel).
    pub counts: Vec<usize>,
}

impl Bin {
    pub fn count(&self) -> usize {
        self.counts.iter().sum()
    }
}

/// `vega-transforms::Bin`'s `_bins` (start/stop/step, re-niced stop) plus
/// its per-row assignment closure, then a count aggregate per bin —
/// production's `{"aggregate": "count"}` y encoding. Empty bins between the
/// first and last populated one ARE included (a real histogram draws a
/// zero-height gap, not a collapsed axis) — Vega ticks every `step` from
/// `start` to `stop` regardless of occupancy.
///
/// `maxbins` is always 10 (VL's `autoMaxBins` default for the x channel —
/// see the module doc's dead-`bin_maxbins` note); a caller never has a
/// board-authored value to pass, so this takes no `maxbins` parameter.
pub fn histogram_bins(values: &[f64]) -> Result<Vec<Bin>, String> {
    let rows: Vec<(f64, Option<usize>)> = values.iter().map(|&v| (v, Some(0))).collect();
    histogram_bins_by(&rows, 1)
}

/// [`histogram_bins`] over `(value, series index)` rows: each bin counts its
/// rows per series. A row with no series (a color Vega-Lite's invalid filter
/// drops) still sets the extent the bins span, but is counted nowhere.
pub fn histogram_bins_by(values: &[(f64, Option<usize>)], series: usize) -> Result<Vec<Bin>, String> {
    let finite: Vec<(f64, Option<usize>)> = values.iter().copied().filter(|(v, _)| v.is_finite()).collect();
    if finite.is_empty() {
        return Err("histogram: no numeric x values to bin".into());
    }
    let min = finite.iter().map(|(v, _)| *v).fold(f64::INFINITY, f64::min);
    let max = finite.iter().map(|(v, _)| *v).fold(f64::NEG_INFINITY, f64::max);
    let (raw_start, raw_stop, step) = vega_bin_step(min, max, 10.0);
    // `_bins`'s own stop re-nice: `start + ceil((bins.stop - start) / step) * step`
    // — belt-and-braces against the float error `vega_bin_step`'s own nice
    // pass can leave (vega/vega#830's "convoluted math for better floating
    // point agreement").
    let stop = raw_start + ((raw_stop - raw_start) / step).ceil() * step;
    let start = raw_start;

    const EPSILON: f64 = 1e-14;
    let n_bins = ((stop - start) / step).round().max(1.0) as usize;
    let mut bins: Vec<Bin> = (0..n_bins)
        .map(|i| Bin {
            start: start + step * i as f64,
            end: start + step * (i as f64 + 1.0),
            counts: vec![0; series],
        })
        .collect();
    for &(v, series_idx) in &finite {
        let Some(series_idx) = series_idx else { continue };
        // Vega's own clamp: outside [start, stop] would be ±Infinity (no
        // row is dropped from the domain the bin: true spec computed this
        // extent FROM, so every value is in range by construction).
        let clamped = v.max(start).min(stop - step);
        let idx = (EPSILON + (clamped - start) / step).floor();
        let idx = idx.clamp(0.0, (n_bins - 1) as f64) as usize;
        bins[idx].counts[series_idx] += 1;
    }
    Ok(bins)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// vega-statistics/test/bin-test.js's own first case: extent [0, 100],
    /// maxbins 10 -> step 10, start 0, stop 100 (already step-aligned, no
    /// nice adjustment needed).
    #[test]
    fn bin_step_matches_the_vega_statistics_fixture() {
        let (start, stop, step) = vega_bin_step(0.0, 100.0, 10.0);
        assert_eq!((start, stop, step), (0.0, 100.0, 10.0));
    }

    /// A span that needs nicing: [3, 97] at maxbins 10 picks step 10 (span
    /// 94 / 10 bins ~= 9.4, rounds to the nearest power-of-base/divide
    /// candidate) and nices the domain out to [0, 100].
    #[test]
    fn bin_step_nices_a_ragged_extent() {
        let (start, stop, step) = vega_bin_step(3.0, 97.0, 10.0);
        assert_eq!(step, 10.0);
        assert_eq!(start, 0.0);
        assert_eq!(stop, 100.0);
    }

    /// Three more cases lifted verbatim from
    /// `vega-statistics/test/bin-test.js` — pinned directly against the
    /// oracle's own fixtures, not just re-derived from the ported algorithm.
    #[test]
    fn bin_step_matches_further_vega_statistics_fixtures() {
        let (start, stop, step) = vega_bin_step(1.3, 10.2, 10.0);
        assert_eq!((start, stop, step), (1.0, 11.0, 1.0));

        let (start, stop, step) = vega_bin_step(99.2258064516129, 2307.451612903226, 30.0);
        assert_eq!((start, stop, step), (0.0, 2400.0, 100.0));

        // Zero-span, min == 0: exercises the `span` fallback's final "else
        // 1.0" arm (both `max - min` and `min.abs()` are 0).
        let (start, stop, step) = vega_bin_step(0.0, 0.0, 10.0);
        assert_eq!((start, stop, step), (0.0, 0.1, 0.1));
    }

    #[test]
    fn ten_bins_span_start_to_stop_with_no_gaps() {
        let bins = histogram_bins(&[3.0, 97.0]).unwrap();
        assert_eq!(bins.first().unwrap().start, 0.0);
        assert_eq!(bins.last().unwrap().end, 100.0);
        for w in bins.windows(2) {
            assert_eq!(w[0].end, w[1].start, "bins tile with no gap");
        }
    }

    #[test]
    fn every_row_lands_in_exactly_one_bin() {
        let values = [1.0, 5.0, 5.0, 9.0, 42.0, 99.0, 100.0];
        let bins = histogram_bins(&values).unwrap();
        let total: usize = bins.iter().map(Bin::count).sum();
        assert_eq!(total, values.len());
        // The max value (100, == stop) lands in the LAST bin, not dropped.
        assert!(bins.last().unwrap().count() >= 1);
    }

    #[test]
    fn empty_input_refuses() {
        assert!(histogram_bins(&[]).is_err());
    }

    #[test]
    fn a_single_repeated_value_still_bins() {
        // span == 0 -> vega-statistics's own `span = min.abs() || 1` branch.
        let bins = histogram_bins(&[5.0, 5.0, 5.0]).unwrap();
        let total: usize = bins.iter().map(Bin::count).sum();
        assert_eq!(total, 3);
    }

    #[test]
    fn empty_rows_refuses() {
        let json = serde_json::json!({
            "resolved": {"chart_type": "histogram"},
            "rows": [],
            "width": 600.0,
            "height": 400.0,
            "background": null,
            "fonts_dir": null,
            "padding": null
        });
        let payload: Payload = serde_json::from_value(json).unwrap();
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        let fonts = FontBook::load_dir(&dir).expect("test fonts load");
        let err = render(&payload, &fonts).expect_err("no rows to bin").to_string();
        assert!(err.contains("histogram"), "{err}");
    }

    #[test]
    fn an_empty_bin_paints_no_rect() {
        // The exact ladder-rung shape: 59 values spanning 167..1014 bin at
        // step 100 and leave 900-1000 empty — Vega's own `aggregate: count`
        // groups the binned field, so that bin draws no mark at all.
        let values: [f64; 59] = [
            468.0, 462.0, 476.0, 654.0, 472.0, 171.0, 573.0, 441.0, 452.0, 525.0, 551.0, 756.0,
            644.0, 524.0, 338.0, 277.0, 554.0, 788.0, 509.0, 477.0, 617.0, 180.0, 431.0, 608.0,
            692.0, 447.0, 583.0, 555.0, 672.0, 255.0, 625.0, 167.0, 366.0, 299.0, 693.0, 646.0,
            232.0, 686.0, 280.0, 481.0, 435.0, 525.0, 680.0, 640.0, 577.0, 643.0, 605.0, 362.0,
            342.0, 397.0, 610.0, 445.0, 1014.0, 320.0, 258.0, 669.0, 813.0, 611.0, 684.0,
        ];
        let bins = histogram_bins(&values).expect("bins");
        let non_empty = bins.iter().filter(|b| b.count() > 0).count();
        assert!(non_empty < bins.len(), "fixture must leave a real gap");

        let rows: Vec<serde_json::Value> =
            values.iter().map(|v| serde_json::json!({"amount": v})).collect();
        let svg = render_ok(minimal_valid_json(None, None, None), serde_json::json!(rows));
        let painted = svg.matches("fill=\"#1c395c\"").count();
        assert_eq!(painted, non_empty, "{svg}");
    }

    /// Review round 2 MEDIUM (`charts/more-types_1`'s 0.0056 CLOSE): Vega's
    /// x/y-axis groups each crisp-shift by +0.5 in both directions
    /// (`docs/laws-shared.md#tickround` — grid/tick RULES already round
    /// their own local position before this shift; LABELS keep the exact
    /// scale position but still inherit the group's own +0.5, the same way
    /// `bar.rs`'s `pos = x_pos(tv) + 0.5` and
    /// `y_label_x = plot_w + 0.5 + y_tick_eff + y_gutter` already do).
    /// Confirmed against a live `vl_convert` render of the real corpus
    /// fixture: the rotated y-title's true pixel position only reconciles
    /// once BOTH this label crisp-shift and its own outer-edge derivation
    /// (below) are fixed together — fixing one alone still misses by 1.5px.
    #[test]
    fn tick_labels_and_rotated_title_carry_the_axis_groups_crisp_shift() {
        let values: [f64; 59] = [
            468.0, 462.0, 476.0, 654.0, 472.0, 171.0, 573.0, 441.0, 452.0, 525.0, 551.0, 756.0,
            644.0, 524.0, 338.0, 277.0, 554.0, 788.0, 509.0, 477.0, 617.0, 180.0, 431.0, 608.0,
            692.0, 447.0, 583.0, 555.0, 672.0, 255.0, 625.0, 167.0, 366.0, 299.0, 693.0, 646.0,
            232.0, 686.0, 280.0, 481.0, 435.0, 525.0, 680.0, 640.0, 577.0, 643.0, 605.0, 362.0,
            342.0, 397.0, 610.0, 445.0, 1014.0, 320.0, 258.0, 669.0, 813.0, 611.0, 684.0,
        ];
        let rows: Vec<serde_json::Value> =
            values.iter().map(|v| serde_json::json!({"amount": v})).collect();
        let svg = render_ok(minimal_valid_json(None, None, None), serde_json::json!(rows));
        // X tick "200" — its own tick mark already rounds+0.5 to 56.500; the
        // label's un-rounded scale position (56.300, before this fix) must
        // still pick up the same group +0.5.
        assert!(svg.contains("x=\"56.800\" y=\"381.500\""), "x label crisp shift: {svg}");
        // Y tick "0" — its label anchor (before this fix: -12.000, 363.000)
        // must pick up +0.5 in both directions too.
        assert!(svg.contains("x=\"-11.500\" y=\"363.500\""), "y label crisp shift: {svg}");
        // The rotated title's outer-edge derivation (`0.5 - y_tick_eff -
        // y_label_pad - y_gutter`) plus this same label crisp shift moves it
        // from the pre-fix -23.58 to -25.08 — the exact combined correction
        // that reconciled the live `vl_convert` position.
        assert!(svg.contains(">#</text>") && svg.contains("x=\"-25.08\""), "title outer edge: {svg}");
    }

    /// A minimal but otherwise-fully-valid resolved histogram — every
    /// required `AxisStyle`/`HistogramStyle` field present — so a color
    /// channel is the ONLY unmodeled thing about it, proving the refusal
    /// fires at the entry gate rather than masking a deserialize failure
    /// the way `empty_rows_refuses`'s bare `{"chart_type": "histogram"}`
    /// does.
    fn minimal_valid_json(
        color: Option<&str>,
        y_label: Option<&str>,
        y_position: Option<&str>,
    ) -> serde_json::Value {
        const TEST_FONT: &str = "'Inter Variable', Inter, sans-serif";
        let axis = |position: Option<&str>| {
            serde_json::json!({
                "grid": {"visible": false, "threshold": null, "width": 1.0, "color": "#D0D3D9", "opacity": 1.0},
                "ticks": {"color": "#D0D3D9", "visible": true, "count": 6.0},
                "labels": {
                    "font": {"family": TEST_FONT, "color": "#626366", "size": 11.0, "weight": "400"},
                    "padding": 6.0,
                    "min_gap": 4.0,
                    "format": ".3~s",
                    "visible": true
                },
                "title": {"font": {"family": TEST_FONT, "weight": "600"}, "padding": 0.0, "visible": true},
                "position": position,
                "fiscal_year_start_month": 1,
                "is_quantitative": true,
                "zero_anchored": false
            })
        };
        serde_json::json!({
            "chart_type": "histogram",
            "field_labels": {},
            "x": "amount",
            "id": "c", "x_label": null,
            "y_label": y_label,
            "title": null,
            "subtitle": null,
            "title_style": null,
            "background": "#ffffff",
            "color": color,
            "resolved_channels": color.map_or_else(
                || serde_json::json!({}),
                |c| serde_json::json!({"color": {"mode": "series", "data_field": c, "quantitative_data": false}}),
            ),
            "palette": ["#3164a3", "#779bc9", "#608470"],
            "legend": {
                "visible": false, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
                "title": {"font": {"family": TEST_FONT, "size": 12.0, "weight": "600", "color": "#313233"}},
                "label": {"font": {"family": TEST_FONT, "size": 11.0, "weight": "400", "color": "#626366"}, "padding": 8.0}
            },
            "style": {
                "axis_x": axis(None),
                "axis_y": axis(y_position.or(Some("left"))),
                "mark": {"border": {"radius": 0.0}, "band_width": 0.9},
                "single_series_fill": "#1c395c",
                "title_font": null
            }
        })
    }

    fn render_ok(resolved: serde_json::Value, rows: serde_json::Value) -> String {
        let json = serde_json::json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": null,
            "fonts_dir": null,
            "padding": null
        });
        let payload: Payload = serde_json::from_value(json).unwrap();
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        let fonts = FontBook::load_dir(&dir).expect("test fonts load");
        render(&payload, &fonts).expect("renders")
    }

    fn render_err(resolved: serde_json::Value, rows: serde_json::Value) -> String {
        let json = serde_json::json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": null,
            "fonts_dir": null,
            "padding": null
        });
        let payload: Payload = serde_json::from_value(json).unwrap();
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        let fonts = FontBook::load_dir(&dir).expect("test fonts load");
        render(&payload, &fonts).expect_err("refuses").to_string()
    }

    /// Series rank by row count, highest first: B (3 rows in the first bin,
    /// 4 overall) takes palette slot 0 and the base of the stack, A (1 row)
    /// slot 1 on top, though A sorts first by name. Only the top segment is
    /// rounded.
    #[test]
    fn a_color_channel_stacks_each_bin_by_series_count_rank() {
        let rows = serde_json::json!([
            {"amount": 1.0, "bucket": "B"}, {"amount": 1.2, "bucket": "B"},
            {"amount": 1.5, "bucket": "B"}, {"amount": 1.8, "bucket": "A"},
            {"amount": 9.0, "bucket": "B"},
        ]);
        let mut resolved = minimal_valid_json(Some("bucket"), None, None);
        resolved["style"]["mark"]["border"]["radius"] = serde_json::json!(2.0);
        let svg = render_ok(resolved, rows);
        let base = svg.find("Bucket: B").expect("series B segment");
        let top = svg.find("Bucket: A").expect("series A segment");
        assert!(base < top, "the most frequent series is the base: {svg}");
        assert!(svg.contains("height=\"270.00\" fill=\"#3164a3\""), "B: slot 0, square, y 90..360: {svg}");
        assert!(svg.contains("V 2.00 A 2.00,2.00") && svg.contains("fill=\"#779bc9\""), "A: slot 1, rounded top: {svg}");
        assert_eq!(svg.matches("aria-roledescription=\"bar\"").count(), 3, "{svg}");
    }

    /// Tied counts rank by name; a board color scale never reaches a histogram.
    #[test]
    fn tied_series_rank_by_name_and_ignore_a_board_color_scale() {
        let rows = serde_json::json!([{"amount": 1.0, "bucket": "B"}, {"amount": 2.0, "bucket": "A"}]);
        let mut resolved = minimal_valid_json(Some("bucket"), None, None);
        resolved["category_colors"] = serde_json::json!([
            {"field": "bucket", "slots": {"B": 5, "A": 6}}
        ]);
        let svg = render_ok(resolved, rows);
        let a = svg.find("Bucket: A").unwrap();
        assert!(svg[..a].rsplit("fill=\"").next().unwrap().starts_with("#3164a3"), "A takes slot 0: {svg}");
    }

    #[test]
    fn a_color_field_without_its_resolved_channel_refuses() {
        let rows = serde_json::json!([{"amount": 1.0, "bucket": "A"}, {"amount": 2.0, "bucket": "B"}]);
        let mut no_channel = minimal_valid_json(Some("bucket"), None, None);
        no_channel["resolved_channels"] = serde_json::json!({});
        assert!(render_err(no_channel, rows).contains("no resolved color channel"));
    }

    #[test]
    fn a_stack_without_a_palette_refuses_and_a_short_top_segment_keeps_its_rounding() {
        let rows = serde_json::json!([{"amount": 1.0, "bucket": "A"}, {"amount": 2.0, "bucket": "B"}]);
        let mut no_palette = minimal_valid_json(Some("bucket"), None, None);
        no_palette["palette"] = serde_json::json!([]);
        assert!(render_err(no_palette, rows).contains("a non-empty palette"));
        // 200 rows of A under one of B: B's top segment is under a pixel tall.
        let mut tall: Vec<serde_json::Value> = (0..200).map(|_| serde_json::json!({"amount": 1.0, "bucket": "A"})).collect();
        tall.push(serde_json::json!({"amount": 1.0, "bucket": "B"}));
        let mut rounded = minimal_valid_json(Some("bucket"), None, None);
        rounded["style"]["mark"]["border"]["radius"] = serde_json::json!(2.0);
        let svg = render_ok(rounded, serde_json::Value::Array(tall));
        assert!(svg.contains("Bucket: B"), "{svg}");
    }

    /// A date-like color is a temporal or ordinal encoding in production: its
    /// own scale and stack order, not the count-rank palette.
    #[test]
    fn a_date_like_color_paints_a_ramp_over_its_sorted_values() {
        let rows = serde_json::json!([
            {"amount": 1.0, "bucket": "2024-01-01"}, {"amount": 2.0, "bucket": "2024-02-01"},
        ]);
        let svg = render_ok(minimal_valid_json(Some("bucket"), None, None), rows);
        assert!(svg.contains("Bucket: Jan 01, 2024") && svg.contains("fill=\"rgb("), "{svg}");
    }

    #[test]
    fn a_null_color_value_stacks_last_and_draws_nothing() {
        let rows = serde_json::json!([{"amount": 1.0, "bucket": "A"}, {"amount": 2.0, "bucket": null}]);
        let svg = render_ok(minimal_valid_json(Some("bucket"), None, None), rows);
        assert!(svg.contains("fill=\"none\"") && svg.contains("Bucket: null"), "{svg}");
    }

    #[test]
    fn a_conditional_color_channel_is_not_a_histogram_payload() {
        let rows = serde_json::json!([{"amount": 1.0, "bucket": "A"}, {"amount": 2.0, "bucket": "B"}]);
        let mut other = minimal_valid_json(Some("bucket"), None, None);
        other["resolved_channels"] = serde_json::json!({"color": {"mode": "conditional", "data_field": "bucket", "quantitative_data": false}});
        assert!(render_err(other, rows).contains("a conditional color channel"));
    }

    fn gradient_json(palette: serde_json::Value) -> serde_json::Value {
        let mut resolved = minimal_valid_json(Some("score"), None, None);
        resolved["resolved_channels"] = serde_json::json!({"color": {
            "mode": "gradient", "data_field": "score", "quantitative_data": true,
            "scale": {"palette": palette, "nice": false, "arm_mode": "asymmetric"}
        }});
        resolved
    }

    fn scored_rows() -> serde_json::Value {
        serde_json::json!([{"amount": 1.0, "score": 1.0}, {"amount": 2.0, "score": 2.0}, {"amount": 3.0, "score": 3.0}])
    }

    /// Vega's interpolator over a one-color range gives no mark a fill, so
    /// production raises its painted-nothing error for the chart.
    #[test]
    fn a_one_color_gradient_paints_no_marks() {
        let err = render_err(gradient_json(serde_json::json!(["#c8281e"])), scored_rows());
        assert!(err.contains("ERR-CHART-PAINTED-NO-MARKS"), "{err}");
    }

    #[test]
    fn a_nan_color_cell_stacks_with_the_nulls() {
        let rows = serde_json::json!([{"amount": 1.0, "score": 1.0}, {"amount": 2.0, "score": 2.0}, {"amount": 3.0, "score": "NaN"}]);
        let svg = render_ok(gradient_json(serde_json::json!(["#ffffcc", "#800026"])), rows);
        assert!(svg.contains("fill=\"rgb(255, 255, 204)\""), "{svg}");
    }

    /// JavaScript reads a cell its `Date.parse` rejects as an Invalid Date, which
    /// the color scale never maps: the bar paints as the unpainted band a null
    /// cell does.
    #[test]
    fn a_date_color_cell_that_is_no_date_paints_the_unpainted_band() {
        let dated = |last: serde_json::Value| {
            let mut rows: Vec<serde_json::Value> =
                (1..=10).map(|d| serde_json::json!({"amount": f64::from(d), "bucket": format!("2024-01-{d:02}")})).collect();
            rows.push(serde_json::json!({"amount": 11.0, "bucket": last}));
            render_ok(minimal_valid_json(Some("bucket"), None, None), serde_json::Value::Array(rows))
        };
        let bad = dated(serde_json::json!("not a date"));
        let null = dated(serde_json::Value::Null);
        let fills = |svg: &str| svg.matches("fill=\"#").count();
        assert_eq!(fills(&bad), fills(&null), "the same marks paint as for a null cell");
    }

    #[test]
    fn an_empty_gradient_palette_refuses_instead_of_panicking() {
        let err = render_err(gradient_json(serde_json::json!([])), scored_rows());
        assert!(err.contains("gradient palette has no colors"), "{err}");
    }

    /// A centered legend taller than its plot keeps production's own coded
    /// refusal rather than an engine-refused wrapper.
    #[test]
    fn a_legend_taller_than_its_plot_keeps_the_align_error_code() {
        let mut resolved = minimal_valid_json(Some("bucket"), None, None);
        resolved["legend"]["visible"] = serde_json::json!(true);
        resolved["legend"]["position"]["align"] = serde_json::json!("center");
        let rows: Vec<serde_json::Value> =
            (0..14).map(|i| serde_json::json!({"amount": f64::from(i), "bucket": format!("B{i:02}")})).collect();
        let payload: Payload = serde_json::from_value(serde_json::json!({
            "resolved": resolved, "rows": rows, "width": 600.0, "height": 160.0,
            "background": null, "fonts_dir": null, "padding": null
        }))
        .unwrap();
        let manifest = env!("CARGO_MANIFEST_DIR");
        let fonts = FontBook::load_dir(&format!("{manifest}/../../../src/dbt_charts/core/render/fonts")).unwrap();
        let err = render(&payload, &fonts).expect_err("a legend taller than its plot refuses");
        assert_eq!(err.to_string().split(':').next(), Some("ERR-LEGEND-ALIGN-UNSUPPORTED"), "{err}");
    }

    #[test]
    fn without_color_channel_still_renders() {
        let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}, {"amount": 3.0}]);
        render_ok(minimal_valid_json(None, None, None), rows);
    }

    /// Same law as `bar.rs`'s canvas-rect test — see
    /// `histogram.rs::Resolved::background`.
    #[test]
    fn canvas_rect_reads_the_chart_own_background_not_the_board_background() {
        let mut resolved = minimal_valid_json(None, None, None);
        resolved["background"] = serde_json::json!("#d9a45b");
        let json = serde_json::json!({
            "resolved": resolved,
            "rows": [{"amount": 1.0}, {"amount": 2.0}, {"amount": 3.0}],
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null
        });
        let payload: Payload = serde_json::from_value(json).unwrap();
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        let fonts = FontBook::load_dir(&dir).expect("test fonts load");
        let svg = render(&payload, &fonts).expect("renders");
        assert!(svg.contains("<rect width=\"600\" height=\"400\" fill=\"#d9a45b\"/>"), "{svg}");
    }

    #[test]
    fn authored_y_label_overrides_the_count_fallback() {
        let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}, {"amount": 3.0}]);
        let svg = render_ok(minimal_valid_json(None, Some("Frequency"), None), rows);
        assert!(svg.contains(">Frequency<"), "{svg}");
        assert!(!svg.contains(">#<"), "{svg}");
    }

    #[test]
    fn empty_y_label_still_falls_back_to_count() {
        // `emitters/bar.py`'s `chart.y_label or COUNT_TITLE` is a falsy
        // check, not a None check — an authored empty string falls back too.
        let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}, {"amount": 3.0}]);
        let svg = render_ok(minimal_valid_json(None, Some(""), None), rows);
        assert!(svg.contains(">#<"), "{svg}");
    }

    /// The count expression's `domain('y')[1]` reaches the tick count as a
    /// `usize`, which only holds if a nice domain over whole counts is whole.
    #[test]
    fn a_nice_count_domain_tops_out_whole() {
        for max_count in 1..=5000 {
            let top = d3_nice_domain(0.0, f64::from(max_count), 10).1;
            assert_eq!(top, top.trunc(), "max count {max_count}");
        }
    }

    /// `set_chart_title` writes a title block only `if title or subtitle`
    /// (`spec_builders.py:156`), so an empty title is no title: the SVG is the
    /// one an unauthored title draws — no empty title `<text>`, no band.
    #[test]
    fn empty_title_and_subtitle_are_no_title() {
        // `truncate` keeps the raw string when the wrap yields no lines, so
        // an empty title that reaches the block draws an empty `<text>`.
        let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}, {"amount": 3.0}]);
        let mut resolved = minimal_valid_json(None, None, None);
        resolved["title_style"] =
            serde_json::json!({"font": {"family": "Inter", "size": 18.0}, "overflow": "truncate"});
        let untitled = render_ok(resolved.clone(), rows.clone());
        resolved["title"] = serde_json::json!("");
        resolved["subtitle"] = serde_json::json!("");
        assert_eq!(render_ok(resolved, rows), untitled);
    }

    #[test]
    fn an_axis_y_position_the_resolver_never_bakes_is_a_payload_error() {
        let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}]);
        let err = render_err(minimal_valid_json(None, None, Some("top")), rows);
        assert_eq!(err, "histogram: axis_y.position \"top\" is neither left nor right");
    }

    fn with_axis_y_mirror(mut resolved: serde_json::Value, mirror: serde_json::Value) -> serde_json::Value {
        resolved["style"]["axis_y"]["mirror"] = mirror;
        resolved
    }

    /// `axis_y.mirror: true` repeats the count labels on the opposite edge, on
    /// either side.
    #[test]
    fn axis_y_mirror_repeats_the_count_labels_on_either_side() {
        for position in [Some("right"), None] {
            let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}]);
            let plain = render_ok(minimal_valid_json(None, None, position), rows.clone());
            let resolved = with_axis_y_mirror(minimal_valid_json(None, None, position), serde_json::json!(true));
            let mirrored = render_ok(resolved, rows);
            assert!(
                mirrored.matches("</text>").count() > plain.matches("</text>").count(),
                "{position:?}: {mirrored}"
            );
        }
    }

    /// A newline in an authored `y_label` reads as a space, like every axis
    /// title, so the title paints one line and reserves one line.
    #[test]
    fn a_newline_in_the_y_label_paints_one_line() {
        let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}]);
        let svg = render_ok(minimal_valid_json(None, Some("Count\nof orders"), None), rows);
        assert!(svg.contains(">Count of orders</text>"), "{svg}");
        assert!(!svg.contains("<tspan"), "{svg}");
    }

    /// Vega-Lite flips a steep tilt's baseline on a right axis: 90 degrees hangs
    /// from the top on the left (`translate(0,9)`) and from the bottom on the
    /// right (`translate(0,-2)`).
    #[test]
    fn a_steep_label_angle_flips_its_baseline_on_a_right_axis() {
        let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}]);
        for (position, shift) in [(None, "rotate(90) translate(0,9)"), (Some("right"), "rotate(90) translate(0,-2)")] {
            let mut resolved = minimal_valid_json(None, None, position);
            resolved["style"]["axis_y"]["labels"]["angle"] = serde_json::json!(90.0);
            let svg = render_ok(resolved, rows.clone());
            assert!(svg.contains(shift), "{position:?}: {svg}");
        }
    }

    /// The mirrored ghost repeats the primary's tilt on the opposite edge.
    #[test]
    fn mirror_repeats_a_label_angle_on_the_opposite_edge() {
        let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}]);
        let tilted = |mirror: bool| {
            let mut resolved = minimal_valid_json(None, None, None);
            if mirror {
                resolved = with_axis_y_mirror(resolved, serde_json::json!(true));
            }
            resolved["style"]["axis_y"]["labels"]["angle"] = serde_json::json!(45.0);
            render_ok(resolved, rows.clone()).matches("rotate(45)").count()
        };
        assert_eq!(tilted(true), 2 * tilted(false));
    }

    /// A right-edge count axis resolves `labels.align: right` (the forced
    /// decimal alignment); with no measurable label padding Vega-Lite drops it,
    /// so the mirror renders instead of refusing.
    #[test]
    fn mirror_renders_a_forced_own_side_align_that_vega_drops() {
        let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}]);
        let mut resolved = with_axis_y_mirror(minimal_valid_json(None, None, Some("right")), serde_json::json!(true));
        resolved["style"]["axis_y"]["labels"]["align"] = serde_json::json!("right");
        render_ok(resolved, rows);
    }

    fn with_axis_y_labels_align_and_domain(
        mut resolved: serde_json::Value,
        align: &str,
        domain: [f64; 2],
    ) -> serde_json::Value {
        resolved["style"]["axis_y"]["labels"]["align"] = serde_json::json!(align);
        resolved["style"]["axis_y"]["scale"]["continuous"]["domain"] = serde_json::json!(domain);
        resolved
    }

    /// The `ladder/histogram/04-right-axis-authored-domain-own-side-align`
    /// chart payload at its production slot (805.04 x 432, 16px padding):
    /// `axis_y` on the right with `labels.align: right` and `labels.format:
    /// ",.0f"`; `position`, `width` and the `scale.continuous.domain` pair are
    /// the parameters, the rest of the scale is the payload's own.
    fn own_side_fixture(position: &str, width: f64, domain: [f64; 2]) -> String {
        let mut payload: serde_json::Value = serde_json::from_str(include_str!(
            "../../../fixtures/histogram/own_side_axis_authored_domain.json"
        ))
        .unwrap();
        payload["width"] = serde_json::json!(width);
        payload["resolved"]["style"]["axis_y"]["position"] = serde_json::json!(position);
        payload["resolved"]["style"]["axis_y"]["labels"]["align"] = serde_json::json!(position);
        payload["resolved"]["style"]["axis_y"]["scale"]["continuous"]["domain"] =
            serde_json::json!(domain);
        let payload: Payload = serde_json::from_value(payload).unwrap();
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        let fonts = FontBook::load_dir(&dir).expect("test fonts load");
        render(&payload, &fonts).expect("renders")
    }

    /// Production's `measure_axis_to_vl` reads the authored
    /// `scale.continuous.domain` before the (empty) data fields, so an
    /// own-side `labels.align` bakes `labelAlign: "right"` and `labelPadding:
    /// 17.64794921875` into the VL y axis (the widest cover-ladder label's
    /// Inter stand-in width plus `VALUE_AXIS_BREATHING_ROOM`; read off the
    /// spec `render_board_svg` hands vl-convert for the fixture board). With
    /// ticks hidden, Vega anchors every
    /// label `end` at `labelPadding` past the axis group (local x 17.648, the
    /// axis group itself at `plot_w + 0.5`) and puts the rotated title
    /// `title.padding` (10) past that anchor, not past the label's far edge.
    /// Production's own SVG: view origin `translate(18,50)`, y-axis group
    /// `translate(732.54,0.5)` (plot 732.04 wide, 343 tall, gridlines
    /// `x2="-732.04"`), labels at local x 17.64794921875 with the "0" label at
    /// y 345, the title at `translate(27.64794921875,171) rotate(90)
    /// translate(0,-2)`.
    ///
    /// Mutation-checked (each fails this test; reverted, not left in the
    /// tree): anchoring `start` instead of `end`; dropping
    /// `VALUE_AXIS_BREATHING_ROOM` from the gutter; reserving the widest
    /// label's ink on top of the measured padding (plot narrows).
    #[test]
    fn right_axis_own_side_align_with_authored_domain_anchors_end_at_the_measured_label_padding() {
        let svg = own_side_fixture("right", 805.04, [0.0, 20.0]);
        assert!(svg.contains("translate(18.000,50.000)"), "plot origin: {svg}");
        assert!(
            svg.contains("<line x1=\"0.5\" y1=\"342.500\" x2=\"732.540\" y2=\"342.500\""),
            "plot is 732.04 wide (production's gridline runs -732.04 from the axis group): {svg}"
        );
        assert!(
            svg.contains("<text x=\"750.188\" y=\"345.500\" text-anchor=\"end\""),
            "label end-anchored at plot_w + 0.5 + labelPadding: {svg}"
        );
        assert!(
            svg.contains("<text x=\"750.188\" y=\"3.500\" text-anchor=\"end\""),
            "top label shares the anchor: {svg}"
        );
        assert!(
            svg.contains("<text x=\"762.19\" y=\"171.50\"")
                && svg.contains("rotate(90 762.19 171.50)"),
            "title sits title.padding + nudge past the label anchor: {svg}"
        );
    }

    /// The mirror image of the right-axis test, at the left fixture's own
    /// production slot (787.84 x 432): `labelAlign: "left"`, the same
    /// `labelPadding: 17.64794921875`, labels `start`-anchored at local x
    /// -17.648 in a y-axis group at `translate(0.5,0.5)`, title at
    /// `translate(-27.64794921875,171) rotate(-90) translate(0,-2)`, view
    /// origin `translate(55,50)`, plot 714.84 wide.
    #[test]
    fn left_axis_own_side_align_with_authored_domain_anchors_start_at_the_measured_label_padding() {
        let svg = own_side_fixture("left", 787.84, [0.0, 20.0]);
        assert!(svg.contains("translate(55.000,50.000)"), "plot origin: {svg}");
        assert!(
            svg.contains("<line x1=\"0.5\" y1=\"342.500\" x2=\"715.340\" y2=\"342.500\""),
            "plot is 714.84 wide (production's gridline runs 714.84 from the axis group): {svg}"
        );
        assert!(
            svg.contains("<text x=\"-17.148\" y=\"345.500\" text-anchor=\"start\""),
            "label start-anchored at 0.5 - labelPadding: {svg}"
        );
        assert!(
            svg.contains("<text x=\"-29.15\" y=\"171.50\"")
                && svg.contains("rotate(-90 -29.15 171.50)"),
            "title sits title.padding + nudge past the label anchor: {svg}"
        );
    }

    #[test]
    fn right_axis_own_side_align_with_domain_and_authored_expr_stays_on_the_default_anchor() {
        // An authored `labels.expr` puts a `labelExpr` in the VL axis dict,
        // which `measure_axis_to_vl` refuses to measure from (it falls back
        // to the un-flipped default), so the label anchor is `start`.
        let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}, {"amount": 3.0}]);
        let mut resolved = with_axis_y_labels_align_and_domain(
            minimal_valid_json(None, None, Some("right")),
            "right",
            [0.0, 20.0],
        );
        resolved["style"]["axis_y"]["labels"]["expr"] = serde_json::json!("datum.label");
        let default_shape = render_ok(minimal_valid_json(None, None, Some("right")), rows.clone());
        assert_eq!(render_ok(resolved, rows), default_shape);
    }

    #[test]
    fn right_axis_own_side_align_without_domain_still_renders() {
        // Own-side align alone (no authored domain) never reaches
        // `measure_axis_to_vl`'s measuring branch -- `tick_values` is empty
        // and `numeric_values(data, ())` is empty too, so production always
        // falls back to the un-flipped default. The refusal gate must not
        // fire on align alone.
        let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}, {"amount": 3.0}]);
        let mut resolved = minimal_valid_json(None, None, Some("right"));
        resolved["style"]["axis_y"]["labels"]["align"] = serde_json::json!("right");
        render_ok(resolved, rows);
    }

    #[test]
    fn right_axis_authored_domain_without_own_side_align_still_renders() {
        // An authored domain alone (no own-side align) never satisfies
        // `measure_axis_to_vl`'s own-side-align gate (`align == orient`), so
        // it stays on the un-flipped default too. The refusal gate must not
        // fire on the domain alone.
        let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}, {"amount": 3.0}]);
        let mut resolved = minimal_valid_json(None, None, Some("right"));
        resolved["style"]["axis_y"]["scale"] =
            serde_json::json!({"continuous": {"domain": [0.0, 20.0]}});
        render_ok(resolved, rows);
    }

    #[test]
    fn right_axis_own_side_align_with_domain_and_upper_case_still_renders() {
        // `vl_field_maps.py`'s own-side-align branch short-circuits on an
        // upper/lower `font.case` before it measures, so production stays
        // on the un-flipped default there. The refusal gate must not fire.
        let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}, {"amount": 3.0}]);
        let mut resolved = with_axis_y_labels_align_and_domain(
            minimal_valid_json(None, None, Some("right")),
            "right",
            [0.0, 20.0],
        );
        resolved["style"]["axis_y"]["labels"]["font"]["case"] = serde_json::json!("upper");
        render_ok(resolved, rows);
    }

    #[test]
    fn right_axis_own_side_align_with_domain_and_no_format_still_renders() {
        // Without a concrete `labels.format` production has no candidate
        // labels to measure, so the measured branch is never taken.
        let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}, {"amount": 3.0}]);
        let mut resolved = with_axis_y_labels_align_and_domain(
            minimal_valid_json(None, None, Some("right")),
            "right",
            [0.0, 20.0],
        );
        resolved["style"]["axis_y"]["labels"]["format"] = serde_json::Value::Null;
        render_ok(resolved, rows);
    }

    /// Production's histogram y encoding carries no `scale`
    /// (`emitters/bar.py::_emit_histogram`), so an authored ascending pair
    /// never reaches Vega: the count axis stays on `[0, d3_nice(0,
    /// max_count, 10).1]` and the render is byte-identical with or without it.
    #[test]
    fn an_ascending_authored_y_domain_leaves_the_count_axis_alone() {
        let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}, {"amount": 3.0}]);
        let plain = render_ok(minimal_valid_json(None, None, Some("right")), rows.clone());
        let mut resolved = minimal_valid_json(None, None, Some("right"));
        resolved["style"]["axis_y"]["scale"] =
            serde_json::json!({"continuous": {"domain": [0.0, 40.0]}});
        assert_eq!(render_ok(resolved, rows), plain);
    }

    /// Production's gutter estimate runs the authored pair through
    /// `min(values)`/`max(values)` (`_measured_label_padding.py:321-325`, fed
    /// the raw pair by `numeric_domain_bounds`, `tick_values.py:58`), so
    /// `[20, 0]` measures exactly what `[0, 20]` does, and the count y-scale
    /// never reads the pair: a descending pair renders its ascending twin's
    /// SVG on either side, own-side aligned or not.
    #[test]
    fn a_descending_authored_y_domain_renders_its_ascending_twin() {
        for position in ["left", "right"] {
            assert_eq!(
                own_side_fixture(position, 805.04, [20.0, 0.0]),
                own_side_fixture(position, 805.04, [0.0, 20.0]),
                "{position}: own-side aligned"
            );
            let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}, {"amount": 3.0}]);
            let mut descending = minimal_valid_json(None, None, Some(position));
            descending["style"]["axis_y"]["scale"] =
                serde_json::json!({"continuous": {"domain": [20.0, 0.0]}});
            let mut ascending = minimal_valid_json(None, None, Some(position));
            ascending["style"]["axis_y"]["scale"] =
                serde_json::json!({"continuous": {"domain": [0.0, 20.0]}});
            assert_eq!(
                render_ok(descending, rows.clone()),
                render_ok(ascending, rows),
                "{position}: default shape"
            );
        }
    }

    /// A non-zero `axis_y.labels.angle` rotates every label about its anchor
    /// on either side; Vega-Lite's default anchor flips past the vertical.
    #[test]
    fn a_labels_angle_rotates_each_label_on_either_side() {
        for position in [None, Some("right")] {
            let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}]);
            let mut resolved = minimal_valid_json(None, None, position);
            resolved["style"]["axis_y"]["labels"]["angle"] = serde_json::json!(45.0);
            let svg = render_ok(resolved, rows);
            assert!(svg.contains("rotate(45) translate(0,3)"), "{position:?}: {svg}");
        }
        let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}]);
        let mut resolved = minimal_valid_json(None, None, None);
        resolved["style"]["axis_y"]["labels"]["angle"] = serde_json::json!(135.0);
        assert!(render_ok(resolved, rows).contains("text-anchor=\"start\" transform=\"translate("));
    }

    #[test]
    fn zero_labels_angle_still_renders() {
        // An authored, literal 0.0 is the same as unauthored -- horizontal
        // labels either way -- so the rotation must not fire on it.
        let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}, {"amount": 3.0}]);
        let mut resolved = minimal_valid_json(None, None, None);
        resolved["style"]["axis_y"]["labels"]["angle"] = serde_json::json!(0.0);
        render_ok(resolved, rows);
    }

    /// The exact ladder-rung fixture (`ladder/histogram/03-right-axis-
    /// refuses`'s own 59-value distribution) rendered with `axis_y.position:
    /// right`, ticks visible (this test fixture's own default, unlike the
    /// real corpus board's `ticks: false`). Pins the three coordinates the
    /// module doc's right-axis law derives: the tick marks point OUTWARD
    /// (increasing x, away from the plot — never inward, which the old
    /// left-axis law would produce), the labels anchor `"start"` at
    /// `plot_w + tick_eff + label_pad + 0.5` (no `VALUE_AXIS_BREATHING_ROOM`
    /// — a wrongly-added `+4.0` here is the law-gap mutation this test
    /// guards against), and the plot negotiates to `563` wide with a `2.0`
    /// free left edge (`AXIS_FREE_EDGE_PAD`, mirrored onto the side with no
    /// value axis). Mutation-checked: flipping the label anchor to `"end"`,
    /// swapping the tick/gutter sign (mirroring the LEFT law instead of the
    /// right), or adding `VALUE_AXIS_BREATHING_ROOM` to the gutter each fail
    /// this test (verified by hand, reverted — not left mutated in the
    /// tree).
    #[test]
    fn right_axis_ticks_point_outward_and_labels_anchor_start_with_no_breathing_room() {
        let values: [f64; 59] = [
            468.0, 462.0, 476.0, 654.0, 472.0, 171.0, 573.0, 441.0, 452.0, 525.0, 551.0, 756.0,
            644.0, 524.0, 338.0, 277.0, 554.0, 788.0, 509.0, 477.0, 617.0, 180.0, 431.0, 608.0,
            692.0, 447.0, 583.0, 555.0, 672.0, 255.0, 625.0, 167.0, 366.0, 299.0, 693.0, 646.0,
            232.0, 686.0, 280.0, 481.0, 435.0, 525.0, 680.0, 640.0, 577.0, 643.0, 605.0, 362.0,
            342.0, 397.0, 610.0, 445.0, 1014.0, 320.0, 258.0, 669.0, 813.0, 611.0, 684.0,
        ];
        let rows: Vec<serde_json::Value> =
            values.iter().map(|v| serde_json::json!({"amount": v})).collect();
        let svg = render_ok(minimal_valid_json(None, None, Some("right")), serde_json::json!(rows));

        // Plot negotiates to 563 wide with a 2.0px free left edge — the
        // group's own translate.
        assert!(svg.contains("translate(2.000,6.000)"), "free left edge: {svg}");

        // Tick marks: outward from the plot's right edge (563 -> 569),
        // never inward toward the plot.
        assert!(
            svg.contains("x1=\"563.000\" y1=\"360.500\" x2=\"569.000\" y2=\"360.500\""),
            "tick points outward from plot edge: {svg}"
        );

        // Labels: anchor "start", x = plot_w(563) + tick_eff(6) +
        // label_pad(6) + 0.5 = 575.5 — no VALUE_AXIS_BREATHING_ROOM term.
        assert!(
            svg.contains("<text x=\"575.500\" y=\"363.500\" text-anchor=\"start\""),
            "label anchors start at plot_w+tick_eff+label_pad+0.5, no breathing room: {svg}"
        );

        // Title "#": past the widest label's own far edge
        // (575.5 + gutter 11.58) plus this test fixture's own
        // title.padding(0.0) + rotated_axis_title_nudge(11px) (2.0) = 589.08,
        // rotated +90 (sign 1.0, the opposite of the left axis's -90).
        assert!(
            svg.contains(">#</text>") && svg.contains("x=\"589.08\" y=\"180.50\""),
            "title past the widest label, rotated +90: {svg}"
        );
        assert!(svg.contains("rotate(90 589.08 180.50)"), "right-axis title rotates +90: {svg}");
    }
}
