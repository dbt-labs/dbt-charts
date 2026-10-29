//! The axes a bar panel draws: the horizontal bar's value axis along the footer, the vertical bar's band
//! and continuous x axes. Each is resolved once by its panel, so `nodes` only draws; a facet composite draws the
//! same value a second time at its footer band's geometry.

use crate::common::{AxisStyle, format_y_label};
use crate::families::axis_caption::AxisCaption;
use crate::families::cartesian::{BandScale, LinearScale};
use crate::families::temporal::{AuthoredTimeFormat, LabelUnit, TemporalLabel, TimeUnit};
use crate::vega_fit;

use measure::FontBook;
use scene::{Attrs, Node};

use super::BarResolved;

/// Vega's `labelFlush` (B8): `labelFlush: true` — VL's default for a
/// continuous x axis, and what production emits here (`axis.labelFlush` is
/// unset in the compiled spec) — compiles to
/// `flush(range("x"), scale("x", datum.value), 1, "left", "right", "center")`
/// (vega-functions `flush`): a **1px proximity test in RANGE-space pixels**,
/// the nearer end winning a tie (`threshold` from `flush_threshold`; `None`
/// when `labels.flush: false` centers every label) — never exact equality on
/// the tick's domain value. `pos` is the tick's own range-space position (`0` at the plot's
/// left edge, `span_px` at its right); the caller passes the SAME
/// coordinate space both call sites use, with no crispness delta folded in
/// (Vega's own `flush` never sees one). A headroom-inflated `domain_max`
/// routinely leaves the last visible tick more than 1px short of the range
/// end; that tick stays centered and reserves half its (prefixed) width as
/// edge chrome. Returns `Some(true)` for the start edge, `Some(false)` for
/// the end edge, `None` for every interior tick.
pub(crate) fn flush_edge(pos: f64, span_px: f64, threshold: Option<f64>) -> Option<bool> {
    let t = threshold?;
    let (l, r) = (pos.abs(), (span_px - pos).abs());
    if l < r && l <= t {
        Some(true)
    } else if r <= t {
        Some(false)
    } else {
        None
    }
}

/// `axis.labels.flush` as Vega-Lite's `labelFlush` reads it: unset or `true`
/// is the 1px threshold, `false` turns flushing off (every label centered),
/// a number is the threshold in range pixels. Anything else refuses.
pub(crate) fn flush_threshold(flush: Option<&serde_json::Value>) -> Result<Option<f64>, String> {
    match flush {
        None | Some(serde_json::Value::Bool(true)) => Ok(Some(1.0)),
        Some(serde_json::Value::Bool(false)) => Ok(None),
        Some(v) => v
            .as_f64()
            .map(Some)
            .ok_or_else(|| format!("bar: axis_y.labels.flush={v} is neither a bool nor a number")),
    }
}

/// Format value-axis tick labels (ruler or d3 format or plain).
///
/// `normalize` forces percent labels regardless of any authored format —
/// Vega-Lite auto-formats a `stack: "normalize"` value axis as a percentage
/// (see `_NORMALIZE_QUARTILE_TICKS` in `emitters/bar.py`).
pub(crate) fn format_tick_labels(
    tick_values: &[f64],
    ax_y: &AxisStyle,
    normalize: bool,
    scale: &'static str,
) -> Result<Vec<String>, String> {
    if normalize {
        return tick_values.iter().map(|&tv| crate::common::normalize_percent_label(tv)).collect();
    }
    tick_values
        .iter()
        .enumerate()
        .map(|(i, &tv)| {
            let label = format_y_label(
                tv,
                ax_y.ruler.as_ref(),
                ax_y.tick_label.as_ref(),
                ax_y.labels.format.as_ref(),
                i,
                tick_values.len(),
            )?;
            ax_y.labels.apply_expr(scale, tv, label, i, tick_values.len())
        })
        .collect()
}

/// A horizontal bar's value axis (labels under the plot, flush-aligned at the
/// range ends, plus the title), resolved once by `render_horizontal_panel`. The
/// panel paints it under its own plot; a facet composite paints the same value
/// at the shared `column_footer`.
#[derive(Clone)]
pub(crate) struct HorizontalValueAxis {
    pub(super) ticks: Vec<f64>,
    pub(super) labels: Vec<String>,
    /// The text each label paints: cut to the axis's `labelLimit`, which its ink (measured on `labels`) is not.
    pub(super) shown: Vec<String>,
    /// `labelOverlap: parity`'s verdict per tick; `None` draws every label.
    pub(super) visible: Option<Vec<bool>>,
    pub(super) domain: (f64, f64),
    pub(super) flush_threshold: Option<f64>,
    pub(super) pad: f64,
    pub(super) font_size: f64,
    pub(super) family: String,
    pub(super) limit: f64,
    pub(super) color: String,
    pub(super) weight: String,
    /// The label font's `style`; `None` writes no `font-style`.
    pub(super) style: Option<String>,
    pub(super) title: String,
    /// The authored label the title edits: none in a facet, whose panels do not pin one.
    pub(super) title_key: Option<crate::contract::LabelKey>,
    pub(super) caption: AxisCaption,
    /// How far the leftmost drawn label reaches left of the plot origin.
    pub(super) left_reach: f64,
    /// How far the rightmost drawn label reaches right of the plot's end.
    pub(super) right_reach: f64,
}

impl HorizontalValueAxis {
    pub(crate) fn left_reach(&self) -> f64 {
        self.left_reach
    }

    pub(crate) fn right_reach(&self) -> f64 {
        self.right_reach
    }

    /// The labels drawn at `plot_w`: each tick's range position, its text and its anchor
    /// (Vega's own `flush` never sees the +0.5 crispness delta).
    pub(super) fn drawn(&self, plot_w: f64) -> Vec<(f64, &str, &str, &'static str)> {
        let linear = LinearScale::new(self.domain.0, self.domain.1, 0.0, plot_w);
        self.ticks
            .iter()
            .zip(&self.labels)
            .enumerate()
            .filter_map(|(i, (&tv, label))| {
                let x = linear.position(tv);
                let hidden = x + 0.5 < -1.0 || x + 0.5 > plot_w + 1.0 || self.visible.as_ref().is_some_and(|v| !v[i]);
                let anchor = match flush_edge(x, plot_w, self.flush_threshold) {
                    Some(true) => "start",
                    Some(false) => "end",
                    None => "middle",
                };
                (!hidden).then_some((x, label.as_str(), self.shown[i].as_str(), anchor))
            })
            .collect()
    }

    /// A centered label on a tick near either end of the plot hangs past it: the
    /// `column-footer` role's bounds, which widen the view's margins.
    pub(super) fn with_reach(mut self, plot_w: f64, fonts: &FontBook) -> Result<Self, String> {
        let (mut left, mut right) = (0.0_f64, 0.0_f64);
        for (x, label, _, anchor) in self.drawn(plot_w) {
            if anchor == "middle" {
                let half = fonts.measure_shaped(label, &self.family, self.font_size)?.min(self.limit) / 2.0;
                left = left.max(half - x);
                right = right.max(x + half - plot_w);
            }
        }
        (self.left_reach, self.right_reach) = (left, right);
        Ok(self)
    }

    /// Paints at the CALLER's own `plot_left`/`plot_w`/`plot_bottom`: the
    /// panel's, or a facet composite's footer band.
    pub(crate) fn nodes(&self, ax: &AxisStyle, plot_left: f64, plot_w: f64, plot_bottom: f64) -> Result<Vec<Node>, String> {
        // The bottom axis group sits at (0.5, ph + 0.5): Vega's crispness
        // translate shifts the whole label row half a pixel right and down.
        let delta = 0.5;
        // Baseline one `round(0.79·fs)` below the label row's top.
        let label_y = plot_bottom + delta + self.pad + measure::baseline_shift("top", self.font_size);
        let mut items: Vec<Node> = self
            .drawn(plot_w)
            .into_iter()
            .map(|(x, _, label, anchor)| {
                Node::text(
                    Attrs::new()
                        .set("x", format!("{:.2}", plot_left + x + delta))
                        .set("y", format!("{label_y:.2}"))
                        .set("text-anchor", anchor)
                        .vega("font-family", self.family.as_str())
                        .set("font-size", self.font_size.to_string())
                        .quick_some("font-style", self.style.as_deref())
                        .set("font-weight", self.weight.as_str())
                        .set("fill", self.color.as_str()),
                    label,
                )
            })
            .collect();
        items.extend(crate::common::axis_title_bottom(
            &self.title,
            &ax.title,
            plot_left + plot_w / 2.0,
            label_y,
            self.font_size,
            self.title_key,
        ));
        crate::contract::axis_group(crate::contract::AxisChannel::X, &self.caption, (plot_left + 0.5, plot_bottom + delta), items)
    }
}

/// How far a rotated label column reaches below its anchor: the widest block's
/// rotated ink (`rotated_category_label_ink`), a second row included. `shaped`
/// measures with the shaped advances the fit measures, else the plain ones.
pub(super) fn rotated_rows_reach(
    rows: &[(String, String)],
    fonts: &FontBook,
    family: &str,
    font_size: f64,
    max_width: Option<f64>,
    (align, baseline, angle): (&str, &str, f64),
    shaped: bool,
    edge: crate::common::XEdge,
) -> Result<f64, String> {
    let limit = max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    let row_w = |row: &str| {
        if row.is_empty() {
            Ok(0.0)
        } else if shaped {
            fonts.measure_shaped(row, family, font_size)
        } else {
            fonts.measure(row, family, font_size)
        }
    };
    rows.iter().try_fold(0.0_f64, |acc, (row1, row2)| {
        let width = row_w(row1)?.max(row_w(row2)?).min(limit);
        let extra = crate::text::line_count(row1) - 1 + usize::from(!row2.is_empty());
        let ink = crate::common::rotated_category_label_ink(0.0, 0.0, width, font_size, align, baseline, angle, extra);
        Ok(acc.max(match edge {
            crate::common::XEdge::Bottom => ink.y2,
            crate::common::XEdge::Top => -ink.y1,
        }))
    })
}

/// The theme's usable label-width ratio, required for date labels.
pub(super) fn cadence_ratio(r: &BarResolved) -> Result<f64, String> {
    r.style.label_usable_ratio.ok_or_else(|| "bar: label_usable_ratio absent (theme required for date labels)".to_string())
}

/// An authored `labels.expr` rewrites every bucket `values` keeps, ungated; a bucket it blanks paints
/// no label and so gets no tick.
#[allow(clippy::too_many_arguments)]
pub(super) fn bar_temporal_labels(
    domain: &[String],
    data_tu: TimeUnit,
    unit: LabelUnit,
    ax_x: &AxisStyle,
    authored: Option<AuthoredTimeFormat>,
    ladder_w: f64,
    chart_w: f64,
    usable_ratio: Option<f64>,
    expr: Option<&crate::labels_expr::Expr>,
    fonts: &FontBook,
    font_size: f64,
) -> Result<crate::families::temporal::OrdinalDateLabels, String> {
    let mut measure = |s: &str| ax_x.labels.measure(fonts, s, font_size);
    let resolved = crate::families::temporal::ordinal_date_labels(
        domain,
        data_tu,
        unit,
        &ax_x.labels,
        ax_x.fiscal_year_start_month,
        authored,
        ladder_w,
        chart_w,
        usable_ratio,
        expr,
        crate::families::temporal::ExprBands::AllBuckets,
        &mut measure,
        font_size,
    )
    .map_err(|e| format!("bar: {e}"))?;
    let labels = ax_x.labels.cased_labels(resolved.labels)?;
    Ok(crate::families::temporal::OrdinalDateLabels { labels, ..resolved })
}

/// The band arm's own x-axis — tick marks, category labels (plain, rotated,
/// or the two-row temporal shape), and the axis title — resolved once by
/// `render_vertical_panel` (font size/family/color, tick length, the
/// temporal-label ladder, the tilt angle) so `paint` only draws, deriving
/// nothing from a raw `AxisStyle` a second time. The panel paints it at
/// its own geometry; a facet composite paints the same value a SECOND time
/// at the shared `column_footer` band's, instead of squeezing the drawing
/// inside one panel's own canvas.
#[derive(Clone)]
pub(crate) struct BandXAxis {
    pub(super) band: BandScale,
    pub(super) cat_domain: Vec<String>,
    /// The category labels as drawn: cut to the axis's `labelLimit`, which their ink is not.
    pub(super) cat_shown: Vec<String>,
    pub(super) temporal_labels: Option<Vec<TemporalLabel>>,
    /// Labels named over a calendar period coarser than the buckets: each label's x and every boundary
    /// tick's x in plot pixels, replacing the per-band ticks and band-centered labels.
    pub(super) interval: Option<BandInterval>,
    pub(super) cat_angle: f64,
    pub(super) cat_baseline: String,
    pub(super) cat_align: String,
    pub(super) x_tick_len: f64,
    pub(super) ax_off_x: f64,
    /// The stroke of the baseline rule on the plot edge this axis hangs from, which its ticks continue.
    pub(super) floor_rule: Option<String>,
    pub(super) cat_pad: f64,
    pub(super) cat_font_size: f64,
    pub(super) cat_family: String,
    pub(super) label_rows: usize,
    /// `labelOverlap: parity` over the drawn temporal labels: the separation
    /// and each label's capped width. Label text only; ticks stay.
    pub(super) parity: Option<(f64, Vec<f64>)>,
    pub(super) cat_title_text: String,
    pub(super) caption: AxisCaption,
    pub(super) title_key: Option<crate::contract::LabelKey>,
    pub(super) edge: crate::common::XEdge,
    /// The drawn rotated label column's reach past the axis pivot — the
    /// widest label's `rotated_category_label_ink(..).y2` at the pivot, the
    /// SAME ink the margin reservation unions. `Some` exactly when the
    /// labels are painted rotated; the title then follows Vega's
    /// `axisTitleLayout` (`s + titlePadding`) instead of the flat row's law.
    pub(super) cat_title_reach: Option<f64>,
}

/// The x a band axis tick at plot position `x` snaps to: the pixel `x` falls in, centered. One snap for every band tick
/// (boundary or band center), so a tick at the plot's left edge lands inside it.
pub(super) fn band_tick_snap(x: f64) -> f64 {
    x.floor() + 0.5
}

#[derive(Clone)]
pub(crate) struct BandInterval {
    pub(super) ticks: Vec<f64>,
    pub(super) labels: Vec<(TemporalLabel, f64)>,
}

/// The x in plot pixels of bucket coordinate `at` on `band` (bucket `i` spans `i..i + 1`): inside a
/// bucket its own bar span, on a boundary the middle of the gap between the neighboring bars. `None`
/// where a bucket the coordinate touches has no band.
pub(super) fn bucket_x(band: &BandScale, domain: &[String], at: f64) -> Option<f64> {
    let edges = |i: usize| band.position(&domain[i]).map(|left| (left, left + band.bandwidth()));
    let i = at.floor() as usize;
    if at.fract() != 0.0 {
        return Some(edges(i)?.0 + at.fract() * band.bandwidth());
    }
    match (i.checked_sub(1).map(edges), domain.get(i).map(|_| edges(i))) {
        (Some(prev), Some(next)) => Some((prev?.1 + next?.0) / 2.0),
        (None, Some(next)) => Some(next?.0),
        (Some(prev), None) => Some(prev?.1),
        (None, None) => None,
    }
}

/// The continuous-x arm's own x-axis (ticks, flush-anchored d3 labels, and
/// the axis title), `BandXAxis`'s twin. `paint`'s `plot_left`/`plot_bottom`
/// are ABSOLUTE: the arm's own call site draws inside a translated `<g>`,
/// so it passes `0.0` there — see that call site's own comment.
#[derive(Clone)]
pub(crate) struct ContinuousXAxis {
    pub(super) x_ticks: Vec<f64>,
    pub(super) x_labels: Vec<String>,
    pub(super) x_lo: f64,
    pub(super) x_span: f64,
    pub(super) x_ticks_visible: bool,
    pub(super) x_tick_len: f64,
    /// The signed distance from the plot edge to the axis group.
    pub(super) x_axis_drop: f64,
    /// The stroke of the baseline rule on the plot edge this axis hangs from, which its ticks continue.
    pub(super) floor_rule: Option<String>,
    pub(super) edge: crate::common::XEdge,
    pub(super) x_tick_eff: f64,
    pub(super) x_label_pad: f64,
    pub(super) x_family: String,
    pub(super) x_font_size: f64,
    pub(super) x_title_text: String,
    pub(super) caption: AxisCaption,
    pub(super) title_key: Option<crate::contract::LabelKey>,
}

impl ContinuousXAxis {
    /// Paints at the CALLER's own `plot_left`/`plot_w`/`plot_bottom` —
    /// the panel's own, or a facet composite's footer band.
    pub(crate) fn nodes(&self, ax_x: &AxisStyle, plot_left: f64, plot_w: f64, (plot_bottom, floor_y): (f64, f64)) -> Result<Vec<Node>, String> {
        let Self { x_ticks, x_labels, .. } = self;
        let (x_family, x_title_text) = (self.x_family.as_str(), self.x_title_text.as_str());
        let Self { x_lo, x_span, x_ticks_visible, x_tick_len, x_axis_drop, x_tick_eff, x_label_pad, x_font_size, .. } =
            *self;
        let x_pos = |tv: f64| plot_left + (tv - x_lo) / x_span * plot_w;
        let sign = self.edge.sign();
        let x_label_y = plot_bottom
            + x_axis_drop
            + sign * (x_tick_eff + x_label_pad)
            + measure::baseline_shift(self.edge.baseline(), x_font_size);
        let mut items: Vec<Node> = Vec::new();
        if x_ticks_visible {
            let end_y = plot_bottom + x_axis_drop + sign * x_tick_len;
            let xs = x_ticks.iter().map(|&tv| x_pos(tv).round() + 0.5);
            items.extend(crate::common::x_tick_marks(&ax_x.ticks, self.floor_rule.as_deref(), &[], xs, (floor_y, end_y), 3)?);
        }
        let x_label_color = ax_x.labels.font.color.as_deref().unwrap_or("#626366");
        let x_label_weight = ax_x.labels.font.weight_str();
        for (&tv, lbl) in x_ticks.iter().zip(x_labels.iter()) {
            let pos = x_pos(tv) + 0.5;
            let anchor = if pos - 0.5 <= plot_left + 1.0 {
                "start"
            } else if pos - 0.5 >= plot_left + plot_w - 1.0 {
                "end"
            } else {
                "middle"
            };
            items.push(Node::text(
                Attrs::new()
                    .set("x", format!("{pos:.3}"))
                    .set("y", format!("{x_label_y:.3}"))
                    .set("text-anchor", anchor)
                    .vega("font-family", x_family)
                    .set("font-size", x_font_size.to_string())
                    .quick_some("font-style", ax_x.labels.font.style.as_deref())
                    .set("font-weight", x_label_weight.as_str())
                    .set("fill", x_label_color),
                lbl.as_str(),
            ));
        }
        items.extend(crate::common::axis_title_flat(
            x_title_text, &ax_x.title, plot_left + plot_w / 2.0, x_label_y, x_font_size, self.title_key, self.edge,
        ));
        crate::contract::axis_group(crate::contract::AxisChannel::X, &self.caption, (plot_left + 0.5, plot_bottom + x_axis_drop), items)
    }
}

impl BandXAxis {
    /// How far a rotated label column reaches below the plot rect, before the
    /// axis group's crisp half pixel (layout bounds are translated by the axis
    /// position alone): a rotated `<text>` carries no `y` attribute, so the
    /// facet composer cannot read this off the painted footer.
    pub(crate) fn rotated_footer_reach(&self) -> Option<f64> {
        self.cat_title_reach.map(|reach| self.ax_off_x + self.x_tick_len + self.cat_pad + reach)
    }

    /// Which temporal labels `labelOverlap: parity` leaves visible, in drawn
    /// order; empty (every label shown) without the transform.
    pub(super) fn parity_shown(&self) -> Vec<bool> {
        let (Some((separation, widths)), Some(tls)) = (&self.parity, &self.temporal_labels) else { return Vec::new() };
        let ink: Vec<vega_fit::Bounds> = tls
            .iter()
            .zip(widths)
            .map(|(tl, &w)| {
                let cx = self.band.position(&self.cat_domain[tl.domain_idx]).map_or(0.0, |x| x + self.band.bandwidth() / 2.0);
                crate::common::rotated_category_label_ink(
                    cx, 0.0, w, self.cat_font_size, &self.cat_align, &self.cat_baseline, self.cat_angle, usize::from(!tl.row2.is_empty()),
                )
            })
            .collect();
        vega_fit::parity_visible(&ink, *separation)
    }

    /// Paints at the CALLER's own `plot_left`/`plot_w`/`plot_bottom` —
    /// the panel's own, or a facet composite's footer band.
    pub(crate) fn nodes(&self, ax_x: &AxisStyle, plot_left: f64, plot_w: f64, (plot_bottom, floor_y): (f64, f64)) -> Result<Vec<Node>, String> {
        let Self { band, cat_domain, cat_shown: cat_labels, .. } = self;
        let (cat_baseline, cat_align, cat_family, cat_title_text) = (
            self.cat_baseline.as_str(),
            self.cat_align.as_str(),
            self.cat_family.as_str(),
            self.cat_title_text.as_str(),
        );
        let Self { cat_angle, x_tick_len, ax_off_x, cat_pad, cat_font_size, label_rows, .. } =
            *self;
        let temporal_labels = self.temporal_labels.as_deref();
        let cat_color = ax_x.labels.font.color.as_deref().unwrap_or("#626366");
        let cat_weight = ax_x.labels.font.weight_str();
        // Vega-fit: the bottom axis group sits at ph + axis offset + the +0.5
        // crispness translate, with the top-baseline shift Vega rounds; band
        // centers stay exact (the band axis bakes the −0.5 back out).
        let sign = self.edge.sign();
        // `plot_bottom` is the y of the plot edge the axis hangs from; Vega's crisp half pixel shifts the group down on either edge.
        let group_y = plot_bottom + sign * ax_off_x + 0.5;
        let cat_label_y = group_y + sign * (x_tick_len + cat_pad) + measure::baseline_shift(self.edge.baseline(), cat_font_size);
        // The rotated labels' anchor, and the pivot the rotated title measures from.
        let anchor_y = cat_label_y - measure::baseline_shift(self.edge.baseline(), cat_font_size);
        let rows_at = |anchor: f64, two: bool| match (self.edge, two) {
            (crate::common::XEdge::Top, true) => (anchor - vega_fit::label_row_advance(cat_font_size), anchor),
            _ => (anchor, anchor + vega_fit::label_row_advance(cat_font_size)),
        };
        let mut items: Vec<Node> = Vec::new();
        let parity_shown = self.parity_shown();
        // Restored tick marks: one per painted label, at the rounded label-center x
        // (Vega's tickRound in the crisp axis frame — the tick rounds
        // `center − 0.5`, the label does not; wk_26 scenegraph: label x 9.15,
        // tick x 9), spanning the axis offset to offset + length below the plot.
        // Thinned labels draw the cadence of the labels: no tick under an unlabeled band.
        if x_tick_len > 0.0 {
            let tick_xs: Vec<f64> = match (&self.interval, &self.temporal_labels) {
                (Some(iv), _) => iv.ticks.clone(),
                (None, Some(tls)) => tls
                    .iter()
                    .enumerate()
                    .filter(|(k, tl)| crate::families::scatter_axis::is_painted(parity_shown.get(*k).copied().unwrap_or(true), &tl.row1))
                    .filter_map(|(_, tl)| band.position(&cat_domain[tl.domain_idx]))
                    .map(|x| x + band.bandwidth() / 2.0)
                    .collect(),
                (None, None) => cat_domain.iter().filter_map(|cat| band.position(cat)).map(|x| x + band.bandwidth() / 2.0).collect(),
            };
            let xs = tick_xs.into_iter().map(|x| plot_left + band_tick_snap(x));
            let end_y = group_y + sign * x_tick_len;
            items.extend(crate::common::x_tick_marks(&ax_x.ticks, self.floor_rule.as_deref(), &[], xs, (floor_y, end_y), 2)?);
        }
        let hidden_at = |k: usize| !parity_shown.get(k).copied().unwrap_or(true);
        let label = |x: f64, y: f64, hidden: bool, text: &str| {
            Node::text(
                Attrs::new()
                    .set("x", format!("{x:.2}"))
                    .set("y", format!("{y:.2}"))
                    .set("text-anchor", "middle")
                    .vega("font-family", cat_family)
                    .set("font-size", cat_font_size.to_string())
                    .quick_some("font-style", ax_x.labels.font.style.as_deref())
                    .set("font-weight", cat_weight.as_str())
                    .set("fill", cat_color)
                    .set_if(hidden, "opacity", "0"),
                text,
            )
        };
        if let Some(tlabels) = temporal_labels.filter(|_| cat_angle == 0.0 && ax_x.labels.visible != Some(false)) {
            let placed: Vec<(usize, f64, &TemporalLabel)> = match &self.interval {
                Some(iv) => iv.labels.iter().enumerate().map(|(k, (tl, x))| (k, plot_left + x, tl)).collect(),
                None => tlabels
                    .iter()
                    .enumerate()
                    .filter_map(|(k, tl)| Some((k, plot_left + band.position(&cat_domain[tl.domain_idx])? + band.bandwidth() / 2.0, tl)))
                    .collect(),
            };
            for (k, label_x, tl) in placed {
                let hidden = hidden_at(k);
                let (row1_y, row2_y) = rows_at(cat_label_y, !tl.row2.is_empty());
                items.push(label(label_x, row1_y, hidden, &tl.row1));
                if !tl.row2.is_empty() {
                    items.push(label(label_x, row2_y, hidden, &tl.row2));
                }
            }
        } else if ax_x.labels.visible == Some(false) {
            // Vega draws no label marks for `labels: false`.
        } else if cat_angle != 0.0 {
            // Vega's SVG text transform for a rotated item: translate to the
            // anchor, rotate, then the baseline shift.
            let dy = measure::baseline_shift(cat_baseline, cat_font_size);
            let text_anchor = match cat_align {
                "left" => "start",
                "right" => "end",
                _ => "middle",
            };
            let drawn: Vec<(&String, &str, &str)> = match temporal_labels {
                Some(tls) => tls.iter().map(|tl| (&cat_domain[tl.domain_idx], tl.row1.as_str(), tl.row2.as_str())).collect(),
                None => cat_domain.iter().zip(cat_labels.iter()).map(|(cat, label)| (cat, label.as_str(), "")).collect(),
            };
            for (k, (cat, row1, row2)) in drawn.into_iter().enumerate() {
                let Some(band_x) = band.position(cat) else { continue };
                let label_x = plot_left + band_x + band.bandwidth() / 2.0;
                items.push(crate::families::heatmap::rotated_label(
                    (label_x, anchor_y, cat_angle, dy),
                    &crate::families::heatmap::LabelFace { places: 2, anchor: text_anchor, family: cat_family, size: cat_font_size, weight: &cat_weight, style: ax_x.labels.font.style.as_deref(), fill: cat_color },
                    (row1, row2),
                    hidden_at(k),
                ));
            }
        } else {
            for (cat, label_text) in cat_domain.iter().zip(cat_labels.iter()) {
                let Some(band_x) = band.position(cat) else { continue };
                let label_x = plot_left + band_x + band.bandwidth() / 2.0;
                items.push(label(label_x, cat_label_y, false, label_text));
            }
        }
        let title_center_x = plot_left + plot_w / 2.0;
        if let Some(reach) = self.cat_title_reach {
            // Vega's `axisTitleLayout` (vega-view-transforms/src/layout/axis.js):
            // the title sits at the label ink's `bounds.y2` past the pivot plus
            // `titlePadding`, then the title text's own "top" baseline shift.
            // The rotated labels' anchor is that pivot.
            let title_fs = ax_x.title.font.size_or(11.0);
            let title_y = anchor_y
                + sign * (reach + ax_x.title.padding.unwrap_or(10.0))
                + measure::baseline_shift(self.edge.baseline(), title_fs);
            items.extend(crate::common::axis_title_at(cat_title_text, &ax_x.title, title_center_x, title_y, self.title_key));
        } else {
            let cat_title_baseline = cat_label_y + sign * (label_rows - 1) as f64 * vega_fit::label_row_advance(cat_font_size);
            items.extend(crate::common::axis_title_flat(
                cat_title_text, &ax_x.title, title_center_x, cat_title_baseline, cat_font_size, self.title_key, self.edge,
            ));
        }
        crate::contract::axis_group(crate::contract::AxisChannel::X, &self.caption, (plot_left + 0.5, group_y), items)
    }
}
