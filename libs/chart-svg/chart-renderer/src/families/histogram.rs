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
//! [`render`] binds, lays out and prints one chart: quantitative binned x (Vega's own
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
//!   both Vega-side constants — see `layout::bar_edges`).
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

pub mod bind;
pub mod layout;

use serde::Deserialize;

use crate::common::{AxisStyle, Font, TitleStyle};
use crate::diagnostic::{warn, Diagnostic};
use crate::layout::Rect;
use measure::{FontBook, Measure};
use print::print;
use crate::Payload;

#[derive(Deserialize)]
pub(crate) struct Resolved {
    pub(crate) id: String,
    pub(crate) x: String,
    pub(crate) x_label: Option<String>,
    #[serde(default)]
    pub(crate) y_label: Option<String>,
    pub(crate) field_labels: crate::common::FieldLabels,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    pub(crate) title: Option<String>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    pub(crate) subtitle: Option<String>,
    pub(crate) title_style: Option<TitleStyle>,
    /// Effective chart background — see `BarResolved::background` and
    /// `docs/laws-shared.md#background`.
    pub(crate) background: String,
    /// Legal and reachable on histogram (unlike `chart.layers`) — see the
    /// module doc.
    pub(crate) color: Option<String>,
    /// Chart-root `ResolvedChart.link`: a channel-placeholder template painted
    /// as an `<a>` around each stack segment.
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    pub(crate) link: Option<String>,
    #[serde(default)]
    pub(crate) link_open: crate::href::LinkOpen,
    /// The host's board URL scheme, from the payload; rewrites each datum href.
    #[serde(skip)]
    pub(crate) link_context: Option<crate::href::LinkContext>,
    /// How the query formats a numeric color column: its gradient legend's labels.
    #[serde(default)]
    pub(crate) color_format: Option<crate::common::FormatSlot>,
    pub(crate) palette: Vec<String>,
    /// The palette a series color reads: the chart's own, else the board's, else Vega's default.
    #[serde(skip)]
    pub(crate) series_palette: Vec<String>,
    #[serde(default)]
    pub(crate) resolved_channels: std::collections::HashMap<String, crate::common::StyleChannel>,
    pub(crate) legend: crate::families::legend::LegendConfig,
    pub(crate) style: HistogramStyle,
}

#[derive(Deserialize)]
pub(crate) struct HistogramStyle {
    pub(crate) axis_x: AxisStyle,
    pub(crate) axis_y: AxisStyle,
    pub(crate) mark: HistogramMark,
    pub(crate) single_series_fill: String,
    pub(crate) title_font: Option<Font>,
    /// The count's card format: with an affix, the card is rewritten row by row
    /// (`NativeTooltipAffixFeature`) and a bin edge below 1 drops its SI letter.
    #[serde(default)]
    pub(crate) tooltip_format: crate::common::FormatSlot,
}

#[derive(Deserialize)]
pub(crate) struct HistogramMark {
    pub(crate) border: HistogramBorder,
    pub(crate) band_width: f64,
}

#[derive(Deserialize)]
pub(crate) struct HistogramBorder {
    pub(crate) radius: f64,
    /// A bar's legend swatch carries the mark's border color as its stroke.
    #[serde(default)]
    pub(crate) color: Option<String>,
}

pub fn render(payload: &Payload, fonts: &FontBook) -> Result<String, Diagnostic> {
    let bound = bind::bind(payload)?;
    let measure = Measure { fonts };
    let cell = Rect { x: 0.0, y: 0.0, width: payload.width, height: payload.height };
    let laid_out = layout::layout(&bound, cell, &measure)?;
    laid_out.diagnostics.into_iter().for_each(warn);
    Ok(print(&laid_out.nodes))
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
    /// Each series' rows grouped by their link, in the order the groups first
    /// appear; empty without a `link`.
    pub hrefs: Vec<Vec<(String, usize)>>,
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
    histogram_bins_by(&rows, 1, &[])
}

/// [`histogram_bins`] over `(value, series index)` rows: each bin counts its
/// rows per series. A row with no series (a color Vega-Lite's invalid filter
/// drops) still sets the extent the bins span, but is counted nowhere.
pub fn histogram_bins_by(values: &[(f64, Option<usize>)], series: usize, hrefs: &[Option<String>]) -> Result<Vec<Bin>, String> {
    let finite: Vec<(usize, (f64, Option<usize>))> = values.iter().copied().enumerate().filter(|(_, (v, _))| v.is_finite()).collect();
    if finite.is_empty() {
        return Ok(Vec::new());
    }
    let min = finite.iter().map(|(_, (v, _))| *v).fold(f64::INFINITY, f64::min);
    let max = finite.iter().map(|(_, (v, _))| *v).fold(f64::NEG_INFINITY, f64::max);
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
            hrefs: vec![Vec::new(); series],
        })
        .collect();
    for &(row, (v, series_idx)) in &finite {
        let Some(series_idx) = series_idx else { continue };
        // Vega's own clamp: outside [start, stop] would be ±Infinity (no
        // row is dropped from the domain the bin: true spec computed this
        // extent FROM, so every value is in range by construction).
        let clamped = v.max(start).min(stop - step);
        let idx = (EPSILON + (clamped - start) / step).floor();
        let idx = idx.clamp(0.0, (n_bins - 1) as f64) as usize;
        bins[idx].counts[series_idx] += 1;
        if let Some(href) = hrefs.get(row).and_then(Option::as_deref) {
            let groups = &mut bins[idx].hrefs[series_idx];
            match groups.iter_mut().find(|(group, _)| group == href) {
                Some((_, n)) => *n += 1,
                None => groups.push((href.to_string(), 1)),
            }
        }
    }
    Ok(bins)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::families::cartesian::d3_nice_domain;

    /// Renders in the en-US time vocabulary the fixtures' dates are drawn in; the
    /// engine entry point scopes the board's own.
    fn render(payload: &Payload, fonts: &FontBook) -> Result<String, Diagnostic> {
        let _en = crate::time_locale::en_us_scope();
        super::render(payload, fonts)
    }

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
        assert!(histogram_bins(&[]).unwrap().is_empty());
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
                "title": {"font": {"case": "title", "family": TEST_FONT, "size": 12.0, "weight": "600", "color": "#313233"}},
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
        assert!(svg.contains("height=\"270.0000\" fill=\"#3164a3\""), "B: slot 0, square, y 90..360: {svg}");
        assert!(svg.contains("V 2.0000 A 2.0000,2.0000") && svg.contains("fill=\"#779bc9\""), "A: slot 1, rounded top: {svg}");
        assert_eq!(svg.matches("aria-roledescription=\"bar\"").count(), 3, "{svg}");
    }

    /// A bin 4.68px wide takes a quarter of its width, not the authored
    /// radius: the corner never reads as a dome.
    #[test]
    fn a_thin_bin_rounds_to_a_quarter_of_its_width() {
        let rows = serde_json::json!([{"amount": 1.0, "bucket": "A"}, {"amount": 9.0, "bucket": "A"}]);
        let mut resolved = minimal_valid_json(Some("bucket"), None, None);
        resolved["style"]["mark"]["border"]["radius"] = serde_json::json!(2.0);
        resolved["style"]["mark"]["band_width"] = serde_json::json!(0.08);
        let svg = render_ok(resolved, rows);
        assert!(svg.contains("A 1.1700,1.1700"), "{svg}");
        assert!(!svg.contains("A 2.0000,2.0000"), "{svg}");
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
    fn a_stack_without_a_palette_takes_vegas_default_and_a_short_top_segment_keeps_its_rounding() {
        let rows = serde_json::json!([{"amount": 1.0, "bucket": "A"}, {"amount": 2.0, "bucket": "B"}]);
        let mut no_palette = minimal_valid_json(Some("bucket"), None, None);
        no_palette["palette"] = serde_json::json!([]);
        let svg = render_ok(no_palette, rows);
        assert!(svg.contains(r##"fill="#4c78a8""##) && svg.contains(r##"fill="#f58518""##), "{svg}");
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

    /// An author's `nice` wins; unset follows the legend, so a hidden legend keeps the drawn extent
    /// (the lowest bar paints the first stop exactly) and a shown one widens to round ends.
    #[test]
    fn a_color_gradient_nices_by_author_else_by_legend() {
        let rows = serde_json::json!([{"amount": 1.0, "score": 12.0}, {"amount": 2.0, "score": 50.0}, {"amount": 3.0, "score": 88.0}]);
        let first_stop = |nice: serde_json::Value, legend: bool| {
            let mut resolved = gradient_json(serde_json::json!(["#000000", "#ffffff"]));
            resolved["resolved_channels"]["color"]["scale"]["nice"] = nice;
            resolved["legend"]["visible"] = serde_json::json!(legend);
            render_ok(resolved, rows.clone()).contains("fill=\"rgb(0, 0, 0)\"")
        };
        assert!(first_stop(serde_json::Value::Null, false));
        assert!(!first_stop(serde_json::Value::Null, true));
        assert!(!first_stop(serde_json::json!(true), false));
        assert!(first_stop(serde_json::json!(false), true));
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

    /// Vega paints an empty color range black.
    #[test]
    fn an_empty_gradient_palette_paints_black() {
        let svg = render_ok(gradient_json(serde_json::json!([])), scored_rows());
        assert!(svg.contains("fill=\"rgb(0, 0, 0)\""), "{svg}");
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
            svg.contains("<line x1=\"0.5\" y1=\"266.500\" x2=\"732.540\" y2=\"266.500\""),
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
            svg.contains("<line x1=\"0.5\" y1=\"266.500\" x2=\"715.340\" y2=\"266.500\""),
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
    // ── The family contract ──────────────────────────────────────────────

    fn test_fonts() -> FontBook {
        let dir = format!("{}/../../../src/dbt_charts/core/render/fonts", env!("CARGO_MANIFEST_DIR"));
        FontBook::load_dir(&dir).expect("test fonts load")
    }

    fn bound(mutate: impl FnOnce(&mut serde_json::Value)) -> (Payload, bind::Bound) {
        let rows: Vec<serde_json::Value> = [3.0, 12.0, 14.0, 47.0, 51.0, 52.0, 97.0]
            .iter()
            .zip(["a", "b", "a", "b", "b", "a", "a"])
            .map(|(amount, grp)| serde_json::json!({"amount": amount, "grp": grp}))
            .collect();
        let mut json = serde_json::json!({
            "resolved": minimal_valid_json(Some("grp"), None, None),
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": null,
            "fonts_dir": null,
            "padding": null
        });
        mutate(&mut json);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let bound = bind::bind(&payload).expect("histogram binds");
        (payload, bound)
    }

    /// `bind` reads no font: the bins, the stacked series with their tooltip text and the tick
    /// labels are there before any size is known.
    #[test]
    fn bind_derives_the_bins_the_stacks_and_the_tick_labels() {
        let (_, bound) = bound(|_| {});
        assert_eq!(bound.bins.len(), 10);
        assert_eq!(bound.x_ticks.len(), 11);
        assert_eq!(bound.x_labels.first().map(String::as_str), Some("0"));
        assert_eq!(bound.bins.iter().map(|bin| bin.count).sum::<usize>(), 7);
        let first = &bound.bins[0];
        let [segment] = first.segments.as_slice() else { panic!("one series in the first bin") };
        let mark = segment.mark.as_ref().expect("the series is drawn");
        assert_eq!(mark.label, "Amount: 0 \u{2013} 10; #: 1; Grp: a");
        assert_eq!((bound.max_count, bound.y_domain_max), (2.0, 2.0));
    }

    #[test]
    fn a_histogram_hugs_nothing_and_takes_the_slot_height() {
        let (payload, bound) = bound(|_| {});
        let fonts = test_fonts();
        let measure = measure::Measure { fonts: &fonts };
        assert_eq!(layout::intrinsic(&bound, &measure).unwrap(), None);
        assert_eq!(layout::height_for_width(&bound, 123.0, &measure).unwrap(), payload.height);
    }

    /// `layout` draws the cell it is given and raises its WARNs itself: the title and the x axis
    /// title are cut, in the order the chart reports them, and none reaches the sink.
    #[test]
    fn layout_draws_the_given_cell_and_returns_its_warnings() {
        let long = "An axis title that keeps going and going until no budget holds it, however it wraps";
        let (_, bound) = bound(|json| {
            let resolved = &mut json["resolved"];
            resolved["title"] = serde_json::json!(long.repeat(3));
            resolved["x_label"] = serde_json::json!(long.repeat(3));
            resolved["style"]["axis_x"]["title"]["visible"] = serde_json::json!(true);
        });
        let fonts = test_fonts();
        let measure = measure::Measure { fonts: &fonts };
        let cell = crate::layout::Rect { x: 0.0, y: 0.0, width: 420.0, height: 260.0 };
        let (laid, sunk) = crate::diagnostic::collect(|| layout::layout(&bound, cell, &measure));
        let laid = laid.expect("histogram lays out");
        assert!(sunk.is_empty(), "{sunk:?}");
        let codes: Vec<_> = laid.diagnostics.iter().map(|d| d.code).collect();
        assert_eq!(codes, ["WARN-CHART-TITLE-TRUNCATED", "WARN-AXIS-TITLE-TRUNCATED"]);
        let [scene::Node::Svg { extent, .. }] = laid.nodes.as_slice() else { panic!("one root") };
        assert_eq!((extent.width, extent.height), (420.0, 260.0));
        let data = laid.data_box;
        assert!(data.x >= 0.0 && data.y >= 0.0 && data.x + data.width <= 420.0 && data.y + data.height <= 260.0);
        assert_eq!(cell.insets_of(&data), laid.insets);
    }

    /// `render` is `bind`, `layout` into the payload's card, and `print`; the warnings reach the
    /// sink once, from the layout's own list.
    #[test]
    fn render_prints_the_layout_of_the_bound_chart() {
        let (payload, bound) = bound(|json| json["resolved"]["title"] = serde_json::json!("T ".repeat(300)));
        let fonts = test_fonts();
        let cell = crate::layout::Rect { x: 0.0, y: 0.0, width: payload.width, height: payload.height };
        let laid = layout::layout(&bound, cell, &measure::Measure { fonts: &fonts }).unwrap();
        let (svg, sunk) = crate::diagnostic::collect(|| render(&payload, &fonts));
        assert_eq!(svg.unwrap(), print::print(&laid.nodes));
        assert_eq!(sunk, laid.diagnostics);
    }
}
