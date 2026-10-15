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
//! `axis_y.mirror` is unmodeled here (production's `mirror_axis.py` does
//! apply to histogram) and refuses by name, same law bar.rs's own
//! `axis_y.mirror` gate uses.
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
//! has real color-channel handling — so a color-grouped histogram is a
//! legitimate, reachable authored shape. Neither `charts/more-types_1` nor
//! the ladder rung author one, and this port does not model the shape
//! (grouped/stacked binned bars, its own legend), so [`render`] refuses by
//! name whenever `resolved.color` is present, rather than silently
//! dropping the channel and drawing a wrong, uncolored chart.

use std::fmt::Write as _;

use serde::Deserialize;

use crate::common::{
    axis_title_text, emit_axis_title_bottom,
    emit_axis_title_rotated, format_y_label, row_num, AxisStyle, Font, TitleStyle,
};
use crate::families::cartesian::{d3_nice_domain, vl_default_tick_count, LinearScale};
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
    /// module doc. Read only to refuse by name; grouped/stacked binned
    /// bars are unmodeled.
    color: Option<String>,
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

/// Rounded-top, square-bottom bar rect — mirrors `bar.rs::emit_column_passes`
/// for the one shape histogram ever draws (no stroke, always a positive
/// count, no double-stroke). `aria` is the bar's description attributes.
fn emit_bar_rect(out: &mut String, x: f64, y: f64, w: f64, h: f64, fill: &str, r: f64, aria: &str) {
    let r = r.min(h / 2.0).min(w / 2.0);
    if r <= 0.0 {
        let _ = write!(
            out,
            "<rect x=\"{x:.2}\" y=\"{y:.2}\" width=\"{w:.2}\" height=\"{h:.2}\" fill=\"{fill}\" stroke-width=\"0\"{aria}/>", fill = xml_escape_attr(fill)
        );
        return;
    }
    let x2 = x + w;
    let y2 = y + h;
    let d = format!(
        "M {x:.2},{y2:.2} V {:.2} A {r:.2},{r:.2} 0 0,1 {:.2},{y:.2} \
         H {:.2} A {r:.2},{r:.2} 0 0,1 {x2:.2},{:.2} V {y2:.2} Z",
        y + r,
        x + r,
        x2 - r,
        y + r,
    );
    let _ = write!(out, "<path d=\"{d}\" fill=\"{fill}\" stroke-width=\"0\"{aria}/>", fill = xml_escape_attr(fill));
}

pub fn render(payload: &Payload, fonts: &FontBook) -> Result<String, String> {
    let r: Resolved = serde_json::from_value(payload.resolved.clone())
        .map_err(|e| format!("histogram: payload.resolved: {e}"))?;
    if r.color.is_some() {
        return Err("histogram: a color channel is not supported".into());
    }
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
        _ => return Err("histogram: axis_y.position other than left or right is not supported".into()),
    };
    // `axis_y.mirror: true` repeats the labels and ticks on the opposite edge at
    // the primary's own offset (`mirror_axis.py`); a ghost `format` refuses.
    let mirror_active = ax_y.mirror_ghost("histogram", false)?.is_some();
    if mirror_active && ax_y.labels.angle.is_some_and(|a| a != 0.0) {
        return Err("histogram: axis_y.mirror with axis_y.labels.angle is not supported".into());
    }
    if mirror_active && ax_y.labels.align.is_some() {
        return Err("histogram: axis_y.mirror with an authored axis_y.labels.align is not supported".into());
    }
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

    let values: Vec<f64> =
        payload.rows.iter().filter_map(|row| row_num(row, &r.x)).collect();
    let bins = histogram_bins(&values)?;
    let bin_start = bins.first().expect("histogram_bins never returns empty").start;
    let bin_stop = bins.last().expect("histogram_bins never returns empty").end;
    if bin_stop <= bin_start {
        return Err("histogram: x domain is degenerate (max ≤ min)".into());
    }
    let max_count = bins.iter().map(|b| b.count).max().unwrap_or(0) as f64;
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

    // ── X: ticks.count reaches VL's tickCount for histogram's x
    // (apply_x_tick_cadence — module doc). Domain is the RAW bin extent,
    // no further d3_nice (the Bin transform's own nice already gives one).
    let x_tick_count = ax_x.ticks.count.unwrap_or(6.0) as usize;
    let x_fmt = ax_x
        .labels
        .spec()
        .ok_or("histogram: axis_x.labels.format missing (theme required)")?;
    let x_ticks_domain = d3rs::ticks::d3_ticks(bin_start, bin_stop, x_tick_count);
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
    // Vega-Lite's default label anchor for a rotated y axis (`defaultLabelAlign`
    // /`defaultLabelBaseline`): straight up or down centers the label, a tilt
    // past the vertical flips its side, and a steep one hangs from the top or
    // bottom. A measured own-side `labelAlign` is authored, so only the
    // baseline follows the angle.
    let y_angle_norm = vega_fit::normalize_angle(y_angle);
    let y_align = if y_labels_flipped || y_angle == 0.0 {
        if y_labels_start { "left" } else { "right" }
    } else if y_angle_norm == 90.0 || y_angle_norm == 270.0 {
        "center"
    } else if y_angle_norm > 90.0 && y_angle_norm < 270.0 {
        if y_labels_start { "right" } else { "left" }
    } else if y_labels_start {
        "left"
    } else {
        "right"
    };
    // Vega-Lite mirrors the baseline on a right axis: a steep tilt hangs from
    // the bottom there where a left axis hangs from the top.
    let y_baseline = if y_angle_norm > 45.0 && y_angle_norm < 135.0 {
        if val_on_right { "bottom" } else { "top" }
    } else if y_angle_norm > 225.0 && y_angle_norm < 315.0 {
        if val_on_right { "top" } else { "bottom" }
    } else {
        "middle"
    };
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

    let x_title_text = axis_title_text(&r.x, r.x_label.as_deref(), &r.field_labels);
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
                    let (x, align) = if val_on_right {
                        (-(y_tick_eff + y_label_gap), "right")
                    } else {
                        (pw + y_tick_eff + y_label_gap, "left")
                    };
                    for (&lw, &tv) in y_label_ws.iter().zip(&y_ticks) {
                        let y = ph * (1.0 - tv / y_domain_max);
                        b.union(&vega_fit::text_ink(x, y, lw.min(y_limit), y_font_size, align, "middle"));
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
                if x_title_extra > 0.0 {
                    let ext = (b.y2 - ph).max(0.0);
                    b.union(&vega_fit::Bounds::new(pw / 2.0 - x_title_w / 2.0, 0.0, pw / 2.0 + x_title_w / 2.0, ph + ext + x_title_extra));
                }
                // Bar rect ink (top corner radius only — never exceeds the
                // straight rect bounds).
                let y0 = ph;
                for bin in &bins {
                    let x_scale = LinearScale::new(bin_start, bin_stop, 0.0, pw);
                    let (left, right) = bar_edges(x_scale.position(bin.start), x_scale.position(bin.end), band_width);
                    let top = ph * (1.0 - bin.count as f64 / y_domain_max);
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
        return Err(e);
    }
    if m.plot_w <= 0.0 || m.plot_h <= 0.0 {
        return Err(format!("histogram: plot box too small ({}×{})", m.plot_w, m.plot_h));
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
        let gc = ax_y.grid.color.as_deref().unwrap_or("#D0D3D9");
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
    let fill = &r.style.single_series_fill;
    crate::contract::mark_group_open(&mut svg, "rect", "marks");
    for bin in bins.iter().filter(|b| b.count > 0) {
        let (left, right) = bar_edges(x_scale.position(bin.start), x_scale.position(bin.end), band_width);
        let top = y_pos(bin.count as f64);
        let fmt = |spec: &str, v: f64| {
            crate::number_locale::d3_format(spec, v).map_err(|e| format!("histogram tooltip format: {e}"))
        };
        // A count axis with no label format prints the bare number.
        let count = match y_fmt_spec {
            Some(spec) => fmt(spec, bin.count as f64)?,
            None => bin.count.to_string(),
        };
        let span = format!("{} \u{2013} {}", fmt(x_fmt, bin.start)?, fmt(x_fmt, bin.end)?);
        let label = crate::contract::native_label(&[(x_title_text.as_str(), span), (y_title_text.as_str(), count)]);
        let aria = crate::contract::aria_attr(Some(&label), crate::contract::ItemRole::Bar);
        emit_bar_rect(&mut svg, left, top, right - left, plot_h - top, fill, corner_r, &aria);
    }
    crate::contract::mark_group_close(&mut svg);

    // X ticks + labels.
    let x_axis_drop = x_axis_offset + 0.5;
    crate::contract::axis_group_open(&mut svg, crate::contract::AxisChannel::X, (0.5, plot_h + x_axis_drop));
    if x_ticks_visible {
        let tc = ax_x.ticks.color.as_deref().unwrap_or("#D0D3D9");
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
    crate::contract::axis_group_open(&mut svg, crate::contract::AxisChannel::Y, (if val_on_right { plot_w + 0.5 } else { 0.5 }, 0.0));
    if y_tick_eff > 0.0 {
        let tc = ax_y.ticks.color.as_deref().unwrap_or("#D0D3D9");
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
    let y_label_anchor = if y_labels_start { "start" } else { "end" };
    for (&tv, lbl) in y_ticks.iter().zip(y_labels.iter()) {
        let shift = vega_fit::baseline_shift(if y_angle == 0.0 { "middle" } else { y_baseline }, y_font_size);
        if y_angle != 0.0 {
            let gy = y_pos(tv) + 0.5;
            let anchor = match y_align {
                "left" => "start",
                "right" => "end",
                _ => "middle",
            };
            let _ = write!(
                svg,
                "<text text-anchor=\"{anchor}\" transform=\"translate({y_label_x:.3},{gy:.3}) \
                 rotate({y_angle}) translate(0,{shift})\" font-family=\"{}\" font-size=\"{y_font_size}\" \
                 font-weight=\"{y_label_weight}\" fill=\"{y_label_color}\">{}</text>",
                vega_escape_attr(y_family),
                xml_escape(lbl), y_label_weight = xml_escape_attr(&y_label_weight), y_label_color = xml_escape_attr(y_label_color)
            );
            continue;
        }
        let gy = y_pos(tv) + 0.5 + shift;
        let _ = write!(
            svg,
            "<text x=\"{y_label_x:.3}\" y=\"{gy:.3}\" text-anchor=\"{y_label_anchor}\" \
             font-family=\"{}\" font-size=\"{y_font_size}\" font-weight=\"{y_label_weight}\" \
             fill=\"{y_label_color}\">{}</text>",
            vega_escape_attr(y_family),
            xml_escape(lbl), y_label_anchor = xml_escape_attr(y_label_anchor), y_label_weight = xml_escape_attr(&y_label_weight), y_label_color = xml_escape_attr(y_label_color)
        );
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
        );
        if y_tick_eff > 0.0 {
            let tc = ax_y.ticks.color.as_deref().unwrap_or("#D0D3D9");
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
        let (ghost_x, ghost_anchor) = if val_on_right {
            (-(y_tick_eff + y_label_gap) + 0.5, "end")
        } else {
            (plot_w + y_tick_eff + y_label_gap + 0.5, "start")
        };
        for (&tv, lbl) in y_ticks.iter().zip(y_labels.iter()) {
            let gy = y_pos(tv) + 0.5 + vega_fit::baseline_shift("middle", y_font_size);
            let _ = write!(
                svg,
                "<text x=\"{ghost_x:.3}\" y=\"{gy:.3}\" text-anchor=\"{ghost_anchor}\" \
                 font-family=\"{}\" font-size=\"{y_font_size}\" font-weight=\"{y_label_weight}\" \
                 fill=\"{y_label_color}\">{}</text>",
                vega_escape_attr(y_family),
                xml_escape(lbl),
                y_label_weight = xml_escape_attr(&y_label_weight),
                y_label_color = xml_escape_attr(y_label_color)
            );
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
    pub count: usize,
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
    let finite: Vec<f64> = values.iter().copied().filter(|v| v.is_finite()).collect();
    if finite.is_empty() {
        return Err("histogram: no numeric x values to bin".into());
    }
    let min = finite.iter().cloned().fold(f64::INFINITY, f64::min);
    let max = finite.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
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
            count: 0,
        })
        .collect();
    for &v in &finite {
        // Vega's own clamp: outside [start, stop] would be ±Infinity (no
        // row is dropped from the domain the bin: true spec computed this
        // extent FROM, so every value is in range by construction).
        let clamped = v.max(start).min(stop - step);
        let idx = (EPSILON + (clamped - start) / step).floor();
        let idx = idx.clamp(0.0, (n_bins - 1) as f64) as usize;
        bins[idx].count += 1;
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
        let total: usize = bins.iter().map(|b| b.count).sum();
        assert_eq!(total, values.len());
        // The max value (100, == stop) lands in the LAST bin, not dropped.
        assert!(bins.last().unwrap().count >= 1);
    }

    #[test]
    fn empty_input_refuses() {
        assert!(histogram_bins(&[]).is_err());
    }

    #[test]
    fn a_single_repeated_value_still_bins() {
        // span == 0 -> vega-statistics's own `span = min.abs() || 1` branch.
        let bins = histogram_bins(&[5.0, 5.0, 5.0]).unwrap();
        let total: usize = bins.iter().map(|b| b.count).sum();
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
        let err = render(&payload, &fonts).expect_err("no rows to bin");
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
        let non_empty = bins.iter().filter(|b| b.count > 0).count();
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
                "ticks": {"visible": true, "count": 6.0},
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
        render(&payload, &fonts).expect_err("refuses")
    }

    #[test]
    fn color_channel_refuses_by_name() {
        let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}]);
        let err = render_err(minimal_valid_json(Some("bucket"), None, None), rows);
        assert_eq!(err, "histogram: a color channel is not supported");
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
    fn unsupported_axis_y_position_refuses_by_name() {
        let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}]);
        let err = render_err(minimal_valid_json(None, None, Some("top")), rows);
        assert_eq!(err, "histogram: axis_y.position other than left or right is not supported");
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

    /// The mirrored ghost copies the primary's label angle and inherited align,
    /// which this arm does not paint: both refuse by name.
    #[test]
    fn mirror_refuses_a_label_angle_and_an_authored_align() {
        let rows = serde_json::json!([{"amount": 1.0}, {"amount": 2.0}]);
        let mut resolved = with_axis_y_mirror(minimal_valid_json(None, None, None), serde_json::json!(true));
        resolved["style"]["axis_y"]["labels"]["angle"] = serde_json::json!(45.0);
        assert_eq!(render_err(resolved, rows.clone()), "histogram: axis_y.mirror with axis_y.labels.angle is not supported");
        let mut resolved = with_axis_y_mirror(minimal_valid_json(None, None, None), serde_json::json!(true));
        resolved["style"]["axis_y"]["labels"]["align"] = serde_json::json!("right");
        assert_eq!(
            render_err(resolved, rows),
            "histogram: axis_y.mirror with an authored axis_y.labels.align is not supported"
        );
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
