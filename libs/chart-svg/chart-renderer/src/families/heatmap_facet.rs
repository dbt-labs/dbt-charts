//! Heatmap small multiples: one band x band panel per baked partition cell,
//! composed by the shared facet composer.
//!
//! Production compiles a `multiples` heatmap to a Vega-Lite `facet` over one
//! `rect` unit. What the composite draws is decided by three facts:
//!
//! * **The color scale is shared** — the gradient ladder is read off every
//!   row, and one horizontal `orient: top` gradient legend hangs above the
//!   grid (the resolver hides its title).
//! * **The x band scale is shared** — its domain is the whole chart's, in
//!   query order (`sort: null`), so a panel missing a month keeps the empty
//!   band. The axis is drawn once per column under the last row.
//! * **The y band scale is shared unless narrowed** — `FacetFeature` frees
//!   it (`resolve.scale.y: independent`) only when a panel holds a proper
//!   subset of the y domain (`_domain_subset_narrowing_candidates`) and
//!   either no columns facet exists or the extra per-column axis is
//!   affordable (`extra_axis_is_affordable`). A narrowed row draws its own
//!   y axis inside its cell; a shared one is a single `row_header` axis at
//!   the left of every grid row.

use crate::diagnostic::Diagnostic;
use std::borrow::Cow;
use std::collections::HashSet;
use std::fmt::Write as _;

use serde_json::{Map, Value};

use crate::common::{dimension_sort_domain, row_str, AxisLabels, AxisStyle, ChartSort, SortAggregate};
use crate::families::axis_caption::category_axis_caption;
use crate::families::bar::CHROME_PX;
use crate::families::cartesian::BandScale;
use crate::families::facet_compose::{
    self, FacetChrome, FacetSpec, GhostStyle, LegendChrome, PanelGeometry, PanelXAxis, RowAxisSpec, TitleChrome,
};
use crate::families::facet_title::{layout_title, TitleLayout, TitleSource};
use crate::families::heatmap::bind::{fold_wide_y, Prelude};
use crate::families::heatmap::{
    dated_bands,
    category_legend_entries, color_categories, date_color, date_color_extent, date_legend_ticks, legend_date_label, ColorKind, drop_invalid_color_rows, GradientColors, SYMBOL_LEGEND_TICK_COUNT, CategoryScale, flat_label, rotated_label, LabelFace, legend_labels, svg_text_anchor, bind_cells, cell_nodes, tooltip_value_title, CellFill, CellMarks, CellPaint, HeaderFmt,
    HeatmapResolved, AXIS_TRANSLATE, BREATHING_ROOM_PX,
};
use crate::families::color_scale::{
    continuous_gradient_def, stops_gradient_def, ContinuousColor, GradientAt, VerticalGradient,
};
use crate::contract;
use measure::baseline_shift;
use print::print;
use scene::{Attrs, Node};
use crate::families::legend::{
    LegendEntry, emit_vega_legend, horizontal_gradient_length, unit_position, vega_legend, LegendConfig, LegendSymbol,
    VEGA_GRADIENT_THICKNESS,
};
use crate::families::multiples::{self, Panel};
use crate::families::temporal::{
    axis_time_format, infer_vega_type, normalize_labeled_temporal, ordinal_date_labels, AuthoredTimeFormat,
    ExprBands, LabelUnit, TemporalLabel, TimeUnit, VegaType,
};
use crate::families::x_type::{parse_date, resolve_cartesian_x_type, Grain, XAxis};
use print::{vega_escape_attr, xml_escape, xml_escape_attr};
use measure::FontBook;
use crate::vega_fit;
use crate::Payload;


/// One drawn x tick: a band index and the (up to two) text rows it carries,
/// each with its measured width for the fit.
#[derive(Clone)]
pub(crate) struct AxisLabel {
    pub(crate) domain_idx: usize,
    pub(crate) row1: String,
    pub(crate) row2: String,
    pub(crate) w1: f64,
    pub(crate) w2: f64,
}

impl AxisLabel {
    pub(crate) fn measured(
        domain_idx: usize,
        row1: String,
        row2: String,
        measure: &dyn Fn(&str) -> Result<f64, String>,
    ) -> Result<Self, String> {
        let width = |row: &str| if row.is_empty() { Ok(0.0) } else { measure(row) };
        Ok(AxisLabel { domain_idx, w1: width(&row1)?, w2: width(&row2)?, row1, row2 })
    }
}

/// The heatmap x band axis a facet composite draws once per column, below the
/// last row, and a single chart draws for a date x: a tick and label at every
/// band for a category x, at every visible opener of the calendar ladder for
/// a date x.
#[derive(Clone)]
pub(crate) struct HeatmapXAxis {
    domain: Vec<String>,
    labels: Vec<AxisLabel>,
    tick_len: f64,
    /// Vega bounds a tick by its full stroke width.
    tick_width: f64,
    offset: f64,
    label_pad: f64,
    font_size: f64,
    family: String,
    /// `labels.visible: false`: the axis keeps its ticks and draws no label.
    labels_hidden: bool,
    /// The rotation of the labels, normalized to `[0, 360)`.
    angle: f64,
    /// Vega's `labelOverlap: parity` over the drawn labels, at `labelSeparation`
    /// (`_generic_layout` asked for it): label text only, never ticks.
    parity_separation: Option<f64>,
    /// The axis title's text; drawn only when the style makes it visible.
    title: String,
    /// The title Vega's axis description names: the drawn one, whoever paints it.
    caption_title: String,
    /// What a visible title adds past the label ink (`x_title_extra`); `0.0` untitled.
    title_extra: f64,
    /// The edge the axis hangs from.
    edge: crate::common::XEdge,
}

impl HeatmapXAxis {
    /// `naive_panel_w` is the emitter's crowding width (`RenderBox.width`,
    /// `facet_panel_width`); `usable_ratio` the theme's `label_usable_ratio`.
    fn resolve(
        domain: &[String],
        grain: Option<TimeUnit>,
        ax_x: &AxisStyle,
        naive_panel_w: f64,
        usable_ratio: f64,
        fonts: &FontBook,
    ) -> Result<Self, String> {
        if let Some(data_tu) = grain {
            return Self::dates(domain, data_tu, ax_x, naive_panel_w, usable_ratio, fonts);
        }
        // `resolve_axis_x_overlap`'s ordinal branch: a category axis whose
        // labels do not fit flat tilts, which production measures on the raw
        // bands even where an authored `labels.expr` rewrites what paints.
        let labels_cfg = &ax_x.labels;
        let angle = vega_fit::normalize_angle(match labels_cfg.angle {
            Some(angle) => angle,
            None => vega_fit::category_label_layout(
                labels_cfg,
                domain,
                naive_panel_w,
                Some(usable_ratio),
                fonts,
                labels_cfg.font.size_or(11.0),
            )?
            .angle,
        });
        let measure = Self::measurer(ax_x, fonts);
        let texts = crate::labels_expr::band_texts("x", domain, labels_cfg).map_err(|e| format!("heatmap: {e}"))?;
        let labels = texts
            .into_iter()
            .enumerate()
            .map(|(domain_idx, d)| AxisLabel::measured(domain_idx, d, String::new(), &measure))
            .collect::<Result<_, _>>()?;
        let mut axis = Self::new(domain, ax_x, labels)?;
        axis.angle = if axis.labels_hidden { 0.0 } else { angle };
        Ok(axis)
    }

    /// The calendar labels of a date x: `resolve_axis_x_overlap`'s temporal
    /// branch keeps only the openers of the grain the crowding width affords
    /// (`resolve_temporal_labels`); an authored `labels.angle` returns before
    /// it, so every bucket speaks at the label grain. `crowd_w` is the chart's
    /// box width (`RenderBox.width`), the same input the ordinal ladder reads.
    pub(crate) fn dates(
        domain: &[String],
        data_tu: TimeUnit,
        ax_x: &AxisStyle,
        crowd_w: f64,
        usable_ratio: f64,
        fonts: &FontBook,
    ) -> Result<Self, String> {
        let bands = dated_bands(domain);
        let dated: Vec<String> = bands.iter().map(|&i| domain[i].clone()).collect();
        let labels_cfg = &ax_x.labels;
        let unit = LabelUnit::resolve(data_tu, labels_cfg.time_unit.as_deref()).map_err(|e| format!("heatmap: {e}"))?;
        let authored = AuthoredTimeFormat::parse(
            "heatmap",
            axis_time_format(labels_cfg.spec(), &ax_x.grain_formats, unit.grain()),
        )?;
        let font_size = labels_cfg.font.size_or(11.0);
        let mut measure = |s: &str| labels_cfg.measure(fonts, s, font_size);
        let expr = labels_cfg
            .expr
            .as_deref()
            .map(|expr| crate::labels_expr::Expr::parse(expr).map(|e| e.on("x")).map_err(|e| format!("heatmap: {e:?}")))
            .transpose()?;
        // A heatmap collapses `values` to the ticks the ladder keeps.
        let resolved = ordinal_date_labels(
            &dated,
            data_tu,
            unit,
            labels_cfg,
            ax_x.fiscal_year_start_month,
            authored,
            crowd_w * usable_ratio,
            crowd_w,
            Some(usable_ratio),
            expr.as_ref(),
            ExprBands::KeptByLadder,
            &mut measure,
            font_size,
        )
        .map_err(|e| format!("heatmap: {e}"))?;
        let angle = labels_cfg.angle.or(resolved.angle);
        let parity = resolved.parity;
        let temporal = labels_cfg.cased_labels(resolved.labels)?;
        let measure = Self::measurer(ax_x, fonts);
        let labels = temporal
            .into_iter()
            .map(|TemporalLabel { domain_idx, row1, row2 }| AxisLabel::measured(bands[domain_idx], row1, row2, &measure))
            .collect::<Result<_, _>>()?;
        let mut axis = Self::new(domain, ax_x, labels)?;
        // Hidden labels paint ticks only, whatever angle is authored.
        axis.angle = if axis.labels_hidden { 0.0 } else { angle.map_or(0.0, vega_fit::normalize_angle) };
        axis.parity_separation = parity.then(|| labels_cfg.min_gap.unwrap_or(0.0));
        Ok(axis)
    }

    /// A label's width as the fit measures it: the raw advance, capped at the
    /// axis label limit Vega enforces.
    fn measurer<'a>(ax_x: &'a AxisStyle, fonts: &'a FontBook) -> impl Fn(&str) -> Result<f64, String> + 'a {
        let font_size = ax_x.labels.font.size_or(11.0);
        let family = ax_x.labels.font.family_or("sans-serif");
        let limit = ax_x.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
        move |text| fonts.measure(text, family, font_size).map(|w| w.min(limit))
    }

    fn new(domain: &[String], ax_x: &AxisStyle, labels: Vec<AxisLabel>) -> Result<Self, String> {
        Ok(HeatmapXAxis {
            domain: domain.to_vec(),
            labels,
            tick_len: if ax_x.ticks.visible.unwrap_or(true) { ax_x.ticks.length.unwrap_or(6.0) } else { 0.0 },
            tick_width: ax_x.ticks.width.unwrap_or(1.0),
            offset: ax_x.ticks.offset.unwrap_or(0.0),
            label_pad: ax_x.labels.padding.ok_or("heatmap: axis_x.labels.padding absent (theme required)")?,
            font_size: ax_x.labels.font.size_or(11.0),
            family: ax_x.labels.font.family_or("sans-serif").to_string(),
            labels_hidden: ax_x.labels.visible == Some(false),
            angle: 0.0,
            parity_separation: None,
            title: String::new(),
            caption_title: String::new(),
            title_extra: 0.0,
            edge: ax_x.x_edge(),
        })
    }

    pub(crate) fn with_title(mut self, title: String, style: &crate::common::AxisTitle) -> Self {
        self.title_extra = crate::common::x_title_extra(style, &title);
        self.caption_title = title.clone();
        self.title = title;
        self
    }

    /// The axis whose title a single chart paints itself.
    pub(crate) fn with_caption_title(mut self, title: &str) -> Self {
        self.caption_title = title.to_string();
        self
    }

    /// One label's ink in the axis frame, below a plot whose bottom edge is at
    /// `row_y - offset - tick_len - label_pad`: a rotated block turns about its
    /// anchor, a flat one is centered on its band. A second row hangs a line
    /// advance below the first.
    fn label_ink(&self, scale: &BandScale, row_y: f64, label: &AxisLabel) -> vega_fit::Bounds {
        let center = scale.position(&self.domain[label.domain_idx]).expect("a label names a band of its own domain")
            + scale.bandwidth() / 2.0;
        let (align, baseline) = self.anchor();
        crate::common::rotated_category_label_ink(
            center,
            row_y,
            label.w1.max(label.w2),
            self.font_size,
            align,
            baseline,
            self.angle,
            usize::from(!label.row2.is_empty()),
        )
    }

    /// A label's `(align, baseline)`: flat ones center on the edge's own baseline, rotated ones
    /// take Vega-Lite's default for the axis side.
    fn anchor(&self) -> (&'static str, &'static str) {
        if self.angle == 0.0 {
            ("center", self.edge.baseline())
        } else {
            vega_fit::vl_x_label_anchor(self.angle, self.edge == crate::common::XEdge::Bottom)
        }
    }

    /// Which labels `labelOverlap: parity` leaves visible below a plot of
    /// `plot_w`; every label without the transform.
    fn visible(&self, plot_w: f64) -> Vec<bool> {
        let Some(separation) = self.parity_separation else { return vec![true; self.labels.len()] };
        let scale = BandScale::new(self.domain.clone(), 0.0, plot_w, 0.0, 0.0);
        let ink: Vec<vega_fit::Bounds> =
            self.labels.iter().map(|label| self.label_ink(&scale, self.label_row(0.0), label)).collect();
        vega_fit::parity_visible(&ink, separation)
    }

    /// The axis frame's label row below a plot whose bottom edge sits at `plot_h`.
    fn label_row(&self, plot_h: f64) -> f64 {
        self.edge.at(plot_h, self.offset + self.tick_len + self.label_pad)
    }

    /// How far a rotated label column reaches below the plot (bounds drop the
    /// axis group's crisp half pixel); `None` for flat labels, whose painted
    /// `y` attributes carry their own reach.
    pub(crate) fn rotated_footer_reach(&self) -> Option<f64> {
        if self.angle == 0.0 {
            // Hidden labels draw no text whose `y` the composer could read off the paint.
            return (self.labels_hidden && self.title_extra > 0.0)
                .then(|| self.offset + self.tick_len + self.tick_width + self.title_extra);
        }
        let row_y = self.label_row(0.0);
        let scale = BandScale::new(self.domain.clone(), 0.0, 1.0, 0.0, 0.0);
        let outer = |ink: vega_fit::Bounds| match self.edge {
            crate::common::XEdge::Bottom => ink.y2,
            crate::common::XEdge::Top => -ink.y1,
        };
        let reach = self.labels.iter().fold(self.edge.sign() * row_y, |acc, label| acc.max(outer(self.label_ink(&scale, row_y, label))));
        Some(reach + self.title_extra)
    }

    /// The chart-wide ladder narrowed to the bands one panel holds: production
    /// resolves the cadence once over the whole chart (`resolve_axis_x_overlap`),
    /// so a panel keeps only the labels that ladder drew on its own bands.
    pub(crate) fn restricted_to(&self, domain: &[String]) -> Self {
        let labels = self
            .labels
            .iter()
            .filter_map(|label| {
                let band = &self.domain[label.domain_idx];
                domain.iter().position(|d| d == band).map(|domain_idx| AxisLabel { domain_idx, ..label.clone() })
            })
            .collect();
        HeatmapXAxis { domain: domain.to_vec(), labels, ..self.clone() }
    }

    /// How far the label rows reach below the axis's label anchor: the
    /// rotated block's own ink, a line (or two) flat, and nothing under hidden
    /// labels, whose axis ends at its ticks.
    pub(crate) fn label_reach(&self) -> f64 {
        if self.labels_hidden {
            return -self.label_pad;
        }
        if self.angle != 0.0 {
            let scale = BandScale::new(self.domain.clone(), 0.0, 1.0, 0.0, 0.0);
            return self.labels.iter().fold(0.0, |acc, label| {
                let ink = self.label_ink(&scale, 0.0, label);
                acc.max(match self.edge {
                    crate::common::XEdge::Bottom => ink.y2,
                    crate::common::XEdge::Top => -ink.y1,
                })
            });
        }
        if self.labels.iter().any(|l| !l.row2.is_empty()) {
            self.font_size + vega_fit::label_row_advance(self.font_size)
        } else {
            self.font_size
        }
    }

    /// Joins every drawn label row to the fit's bounds, below a plot
    /// `plot_w` x `plot_h`. A second row sits `fontSize + 2` under the first.
    pub(crate) fn union_ink(&self, b: &mut vega_fit::Bounds, plot_w: f64, plot_h: f64) {
        self.union_label_ink(b, plot_w, plot_h);
        if self.title_extra > 0.0 {
            let (y1, y2) = match self.edge {
                crate::common::XEdge::Bottom => (b.y2, b.y2 + self.title_extra),
                crate::common::XEdge::Top => (b.y1 - self.title_extra, b.y1),
            };
            b.union(&vega_fit::Bounds::new(plot_w / 2.0, y1, plot_w / 2.0, y2));
        }
    }

    fn union_label_ink(&self, b: &mut vega_fit::Bounds, plot_w: f64, plot_h: f64) {
        let scale = BandScale::new(self.domain.clone(), 0.0, plot_w, 0.0, 0.0);
        if self.labels_hidden {
            let tick_far = self.edge.at(plot_h, self.offset + self.tick_len);
            let tick_near = self.edge.at(plot_h, self.offset);
            for label in &self.labels {
                let x = scale.position(&self.domain[label.domain_idx]).expect("a band of its own domain")
                    + scale.bandwidth() / 2.0;
                let tick_x = x.floor() + AXIS_TRANSLATE;
                b.union(&vega_fit::Bounds::new(tick_x, tick_near.min(tick_far), tick_x, tick_near.max(tick_far)));
            }
            return;
        }
        let row_y = self.label_row(plot_h);
        for (label, _) in self.labels.iter().zip(self.visible(plot_w)).filter(|(_, visible)| *visible) {
            let mut ink = self.label_ink(&scale, row_y, label);
            // A band label sits inside the axis group's crisp half-pixel translate.
            ink.x1 -= AXIS_TRANSLATE;
            ink.x2 += AXIS_TRANSLATE;
            b.union(&ink);
        }
    }

    /// How the labels lie, for an expression that reads its label `item`.
    pub(crate) fn item_axis(&self, ax_x: &AxisStyle) -> Result<crate::labels_expr::view::AxisSpec, String> {
        use crate::labels_expr::view::{anchor, AxisSpec, Orient};
        let (align, baseline) = if self.angle == 0.0 { ("center", "top") } else { vega_fit::vl_x_label_anchor(self.angle, true) };
        let (align, baseline) = (Some(anchor(align)?), Some(anchor(baseline)?));
        let (offset, angle) = (self.tick_len + self.label_pad, vega_fit::normalize_angle(self.angle));
        Ok(AxisSpec { orient: Orient::Bottom, offset, shift: -0.5, align, baseline, angle, flush: None, style: ax_x.labels.item_style()? })
    }

    /// The axis at the CALLER's `plot_left`/`plot_w`/`plot_edge`, the y of the plot edge the axis
    /// hangs from. The axis group sits `offset` plus Vega's half-pixel crisp translate past
    /// it; a tick snaps to a whole pixel (`floor(center) + 0.5`) while its label keeps the
    /// exact band center.
    pub(crate) fn nodes(&self, ax_x: &AxisStyle, plot_left: f64, plot_w: f64, (plot_edge, floor_y): (f64, f64)) -> Result<Vec<Node>, String> {
        let scale = BandScale::new(self.domain.clone(), 0.0, plot_w, 0.0, 0.0);
        let sign = self.edge.sign();
        let axis_top = plot_edge + sign * self.offset + AXIS_TRANSLATE;
        let label_y = axis_top + sign * (self.tick_len + self.label_pad) + measure::baseline_shift(self.edge.baseline(), self.font_size);
        let row_advance = vega_fit::label_row_advance(self.font_size);
        let label_color = ax_x.labels.font.color.as_deref().unwrap_or("#626366");
        let label_weight = ax_x.labels.font.weight_str();
        let caption = category_axis_caption(&self.caption_title, ax_x, &self.domain)?;
        let (align, baseline) = self.anchor();
        let anchor_y = axis_top + sign * (self.tick_len + self.label_pad);
        let dy = measure::baseline_shift(baseline, self.font_size);
        // A flat label is `middle`-anchored whatever the side; a rotated one follows the angle.
        let face = LabelFace { places: 2, anchor: svg_text_anchor(align), family: &self.family, size: self.font_size, weight: &label_weight, fill: label_color };
        let flat_face = LabelFace { anchor: "middle", ..face };
        let visible = self.visible(plot_w);
        let center_of = |label: &AxisLabel| {
            scale.position(&self.domain[label.domain_idx]).expect("a label names a band of its own domain") + scale.bandwidth() / 2.0
        };
        let mut items = Vec::new();
        if self.tick_len > 0.0 {
            let xs = self.labels.iter().map(|label| plot_left + center_of(label).floor() + AXIS_TRANSLATE);
            items.extend(crate::common::x_tick_marks(&ax_x.ticks, None, &[], xs, (floor_y, axis_top + sign * self.tick_len), 2)?);
        }
        for (label, &shown) in self.labels.iter().zip(&visible) {
            let center = center_of(label);
            if self.labels_hidden {
                continue;
            }
            if self.angle != 0.0 {
                items.push(rotated_label(
                    (plot_left + center, anchor_y, self.angle, dy),
                    &face,
                    (&label.row1, &label.row2),
                    !shown,
                ));
                continue;
            }
            // The label block anchors on its far baseline: a second row hangs below the first on
            // the bottom edge and the first sits above the second on the top.
            let (row1_y, row2_y) = match self.edge {
                crate::common::XEdge::Bottom => (label_y, label_y + row_advance),
                crate::common::XEdge::Top if label.row2.is_empty() => (label_y, label_y),
                crate::common::XEdge::Top => (label_y - row_advance, label_y),
            };
            for (text, y) in [(&label.row1, row1_y), (&label.row2, row2_y)] {
                if !text.is_empty() {
                    items.push(flat_label((plot_left + center, y), &flat_face, !shown, text));
                }
            }
        }
        if self.angle != 0.0 || self.labels_hidden {
            // `axisTitleLayout`: the title sits at the axis bounds' bottom plus its padding.
            let bounds_bottom = if self.angle != 0.0 {
                self.rotated_footer_reach().expect("a rotated axis has a footer reach") - self.title_extra - self.offset
            } else {
                self.tick_len + self.tick_width
            };
            let title_fs = ax_x.title.font.size_or(11.0);
            let title_y = axis_top
                + sign * (bounds_bottom + ax_x.title.padding.unwrap_or(10.0))
                + measure::baseline_shift(self.edge.baseline(), title_fs);
            items.extend(crate::common::axis_title_at(&self.title, &ax_x.title, plot_left + plot_w / 2.0, title_y, None));
        } else if crate::common::axis_title_band(&ax_x.title, &self.title) > 0.0 {
            let rows_offset = if self.labels.iter().any(|l| !l.row2.is_empty()) {
                vega_fit::label_row_advance(self.font_size)
            } else {
                0.0
            };
            items.extend(match self.edge {
                crate::common::XEdge::Bottom => crate::common::axis_title_bottom(
                    &self.title,
                    &ax_x.title,
                    plot_left + plot_w / 2.0,
                    label_y + rows_offset,
                    self.font_size,
                    None,
                ),
                crate::common::XEdge::Top => crate::common::axis_title_at(
                    &self.title,
                    &ax_x.title,
                    plot_left + plot_w / 2.0,
                    label_y - rows_offset - self.font_size - ax_x.title.padding.unwrap_or(10.0),
                    None,
                ),
            });
        }
        contract::axis_group(contract::AxisChannel::X, &caption, (plot_left + AXIS_TRANSLATE, axis_top), items)
    }
}

/// The width one extra per-column-panel y axis would cost
/// (`facet_extra_axis_width_px`, `_cartesian.py:1081-1156`): the chrome plus the
/// widest band label, clamped to the label limit. The labels are measured at the
/// weight they paint at (`:1151`), so a bold axis costs more than a regular one.
pub(crate) fn facet_extra_axis_px(labels: &AxisLabels, bands: &[String], fonts: &FontBook) -> Result<f64, String> {
    let size = labels.font.size_or(11.0);
    let limit = labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    let widest = bands.iter().try_fold(0.0_f64, |acc, band| {
        labels.measure(fonts, band, size).map(|w| acc.max(w))
    })?;
    Ok(CHROME_PX + widest.min(limit))
}

/// The values a field takes inside one panel. A partition field is not in a
/// panel's rows (`regroup` strips it); its one value is the panel's key.
fn panel_values(panel: &Panel, axes: &[multiples::PartitionAxis], field: &str) -> HashSet<String> {
    match axes.iter().position(|a| a.field == field) {
        Some(pos) => HashSet::from([facet_compose::facet_value_label(&panel.key[pos])]),
        None => panel.rows.iter().map(|row| row_str(row, field)).filter(|v| !v.is_empty()).collect(),
    }
}

/// `_panel_domain_is_proper_subset`: some panel holds fewer of the field's
/// values than the whole chart does (a field with one value overall has no
/// narrower state).
pub(crate) fn panel_domain_is_proper_subset(panels: &[Panel], axes: &[multiples::PartitionAxis], field: &str) -> bool {
    let per_panel: Vec<HashSet<String>> = panels.iter().map(|p| panel_values(p, axes, field)).collect();
    let whole: HashSet<&String> = per_panel.iter().flatten().collect();
    whole.len() > 1 && per_panel.iter().any(|values| values.len() < whole.len())
}

/// Distinct values of `field` in first-appearance order — Vega-Lite's domain
/// for a nominal scale pinned `sort: null`.
pub(crate) fn first_appearance(rows: &[Map<String, Value>], field: &str) -> Vec<String> {
    let mut seen = HashSet::new();
    rows.iter().map(|row| row_str(row, field)).filter(|v| seen.insert(v.clone())).collect()
}

/// A narrowed panel's domain for `field`: the bands its own rows hold, in
/// first-appearance order, or ranked by the authored sort over those rows
/// (Vega sorts each panel's scale natively; only a shared domain is pinned).
fn narrowed_domain(rows: &[Map<String, Value>], field: &str, sort: Option<&ChartSort>) -> Result<Vec<String>, String> {
    let domain = first_appearance(rows, field);
    match sort {
        Some(sort) => {
            let refs: Vec<&Map<String, Value>> = rows.iter().collect();
            crate::common::min_ranked_domain(domain, &refs, |row| row_str(row, field), sort).map_err(|e| format!("heatmap: {e}"))
        }
        None => Ok(domain),
    }
}

/// A heatmap dimension's rows as production's emitter hands them to the band
/// scale (`canonicalize_cartesian_x_data`), and the calendar grain
/// `resolve_cartesian_x_type` gives it. A labeled bucket ("2024-Q1") becomes
/// its ISO date. Under a calendar grain a date-time collapses to its date and
/// an x sorts ascending whatever the query order (`sort=True` for x only; a
/// y keeps the query's order). A y without a calendar grain keeps its rows
/// untouched, as the emitter only canonicalizes a dated y; an authored grain
/// over a column no date reads gives no ladder either.
pub(crate) fn date_dimension(
    rows: &[Map<String, Value>],
    field: &str,
    axis: &AxisStyle,
    is_x: bool,
) -> Result<(Vec<Map<String, Value>>, Option<TimeUnit>), String> {
    let law_axis = XAxis::of(axis);
    let law = |rows: &[Map<String, Value>]| resolve_cartesian_x_type(rows, field, &law_axis, "heatmap", false, &[]);
    let normalized = if is_x { normalize_labeled_temporal(rows, field)? } else { Cow::Borrowed(rows) };
    let resolution = law(&normalized)?;
    let Grain::Calendar(tu) = resolution.grain else { return Ok((normalized.into_owned(), None)) };
    if !matches!(resolution.from_data, VegaType::Temporal | VegaType::Ordinal) {
        return Ok((normalized.into_owned(), None));
    }
    let mut rows = if is_x { normalized.into_owned() } else { normalize_labeled_temporal(&normalized, field)?.into_owned() };
    if infer_vega_type(&rows, field) == VegaType::Temporal {
        for row in &mut rows {
            if let Some(((y, m, d), _)) = row.get(field).and_then(Value::as_str).and_then(parse_date) {
                row.insert(field.to_string(), Value::String(format!("{y:04}-{m:02}-{d:02}")));
            }
        }
        if is_x {
            // `canonicalize_and_sort_ordinal_x` sorts on `str(value)`, so a null sorts as "None", after every date.
            rows.sort_by_key(|row| match row.get(field) {
                None | Some(Value::Null) => "None".to_string(),
                Some(_) => row_str(row, field),
            });
        }
    }
    Ok((rows, Some(tu)))
}

/// Everything a panel paint reads that is common to the whole composite.
struct PanelStyle<'a> {
    ax_x: &'a AxisStyle,
    ax_y: &'a AxisStyle,
    /// Every panel draws its own x axis inside its cell.
    cell_x_axis: bool,
    cells: CellPaint<'a>,
    marks: CellMarks,
    y_font_size: f64,
    y_family: &'a str,
    y_color: &'a str,
    y_weight: String,
    y_label_ws: &'a [(String, f64)],
    /// `labelPadding`: where a left-anchored label starts, left of the plot.
    y_label_pad: f64,
    /// How far left of the plot the row labels reach: their anchor, plus their own width when
    /// they hang away from the axis (`text-anchor: end`).
    y_label_reach: f64,
    /// The left axis's labels end at their anchor (Vega-Lite's away-side default) rather than start there.
    y_labels_end: bool,
    y_tick_len: f64,
    /// A narrowed y draws its axis inside every cell; a shared one is the
    /// composer's row-header axis.
    own_axis: bool,
    /// `axis_y.position: right`: the shared axis is a `row_footer` at the last
    /// column of each row rather than the composer's left `row_header`.
    y_right: bool,
    /// The labels sit on their own side of the axis (`heatmap::y_labels_own_side`).
    y_own_side: bool,
    y_ticks: &'a crate::common::AxisTicks,
    y_title: &'a str,
    y_title_style: &'a crate::common::AxisTitle,
    /// What a visible y title adds left of the label band (`y_title_extra`).
    y_title_extra: f64,
    /// `axis_y.mirror`: the shared axis stays on the left and its labels repeat
    /// on the right edge (a `row_footer` without the title), starting at the
    /// primary's own `labelPadding` (`y_label_pad`).
    mirror: bool,
    /// The ghost's own text for each band when its format relabels them.
    ghost_texts: Option<&'a std::collections::HashMap<String, String>>,
}

impl PanelStyle<'_> {
    /// How far past the plot the right-hand axis's labels reach: a right axis's own, or the mirror
    /// ghost's, which starts at the primary's `labelPadding`.
    fn right_reach(&self) -> f64 {
        if self.y_right { self.y_label_reach } else { self.y_label_pad }
    }
}

/// Where a panel sits in a facet row, for the right-side shared axis.
struct RightAxis {
    /// Only the last column of a row paints the axis.
    paint: bool,
    /// A rows-only facet folds the gutter into the cell's ink.
    fold: bool,
    /// `ceil(cell x2) - plot_w`: where `row_footer` anchors past the plot.
    overhang: f64,
}

fn render_panel(
    style: &PanelStyle,
    rows: &[Map<String, Value>],
    (x_domain, x_axis): (&[String], &HeatmapXAxis),
    y_domain: &[String],
    plot_w: f64,
    plot_h: f64,
    right: &RightAxis,
) -> Result<(String, PanelGeometry), String> {
    let x_scale = BandScale::new(x_domain.to_vec(), 0.0, plot_w, 0.0, 0.0);
    let y_scale = BandScale::new(y_domain.to_vec(), 0.0, plot_h, 0.0, 0.0);
    let shown = |band: &String| style.ax_y.labels.cased(band);
    let ghost_text = |band: &String| style.ax_y.labels.cased(style.ghost_texts.and_then(|texts| texts.get(band)).unwrap_or(band));
    let label_w = |band: &str| {
        style.y_label_ws.iter().find(|(label, _)| label == band).map(|(_, w)| *w).expect("label measured")
    };
    // A cell's own axis sits on the left of its plot, or on the right when
    // `axis_y.position` says so, where the labels start past the tick.
    let own_right = style.own_axis && style.y_right;
    let label_x = if own_right {
        plot_w + AXIS_TRANSLATE + style.y_label_pad
    } else {
        AXIS_TRANSLATE - style.y_label_pad
    };
    let y_align = if (own_right && style.y_own_side) || (!own_right && style.y_labels_end) { "right" } else { "left" };
    let centers: Vec<(&String, f64)> = y_domain
        .iter()
        .map(|band| (band, y_scale.position(band).expect("band in its own domain") + y_scale.bandwidth() / 2.0))
        .collect();
    // Hanging away from the axis, a label bounds at its own geometry, without the axis group's crisp translate.
    let label_ink = |cy: f64, w: f64| vega_fit::text_ink(label_x - if style.y_labels_end { AXIS_TRANSLATE } else { 0.0 }, cy, w, style.y_font_size, y_align, "middle");
    let own_title_ink = |plot_h: f64| {
        if own_right {
            let edge = plot_w + AXIS_TRANSLATE + style.y_label_reach;
            vega_fit::Bounds::new(edge, 0.0, edge + style.y_title_extra, plot_h)
        } else {
            let outer = AXIS_TRANSLATE - style.y_label_reach;
            vega_fit::Bounds::new(outer - style.y_title_extra, 0.0, outer, plot_h)
        }
    };
    let margins = vega_fit::pad_plot(plot_w, plot_h, |_, _, b| {
        b.union(&vega_fit::Bounds::new(0.0, 0.0, plot_w, plot_h).expand(style.marks.reach));
        if style.own_axis {
            for &(band, cy) in &centers {
                b.union(&label_ink(cy, label_w(band)));
            }
            if style.y_title_extra > 0.0 {
                b.union(&own_title_ink(plot_h));
            }
        }
        if style.cell_x_axis {
            x_axis.union_ink(b, plot_w, plot_h);
        }
    }, None);
    let left = margins.left;

    let mut svg = String::with_capacity(4096);
    let (canvas_w, canvas_h) = (left + plot_w + margins.right, plot_h + margins.bottom);
    let _ = write!(
        svg,
        "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{canvas_w}\" height=\"{canvas_h}\" \
         viewBox=\"0 0 {canvas_w} {canvas_h}\">"
    );
    let content_start = svg.len();
    let _ = write!(svg, "<g transform=\"translate({left:.2},0)\">");
    let cells = bind_cells(rows, x_scale.domain(), y_scale.domain(), &style.cells)?;
    svg.push_str(&print(&cell_nodes(&cells, &x_scale, &y_scale, &style.marks)?));
    svg.push_str("</g>");
    let label_shift = measure::baseline_shift("middle", style.y_font_size);
    let y_caption = category_axis_caption(style.y_title, style.ax_y, y_domain)?;
    if style.own_axis && !own_right {
        crate::contract::axis_group_open(&mut svg, crate::contract::AxisChannel::Y, &y_caption, (left + label_x, 0.0))?;
        crate::common::emit_value_ticks(
            &mut svg,
            &style.y_ticks,
            centers.iter().map(|&(_, cy)| cy),
            left + AXIS_TRANSLATE,
            -1.0,
            style.y_tick_len,
            &[],
        )?;
        for &(band, cy) in &centers {
            let text = shown(band)?;
            let _ = write!(
                svg,
                "<text x=\"{:.2}\" y=\"{:.2}\" text-anchor=\"{}\" font-family=\"{}\" font-size=\"{}\" \
                 font-weight=\"{}\" fill=\"{}\">{}</text>",
                left + label_x,
                cy + label_shift,
                if style.y_labels_end { "end" } else { "start" },
                vega_escape_attr(style.y_family),
                style.y_font_size,
                xml_escape_attr(&style.y_weight),
                xml_escape_attr(style.y_color),
                xml_escape(&text)
            );
        }
        crate::common::emit_axis_title_rotated(
            &mut svg,
            style.y_title,
            style.y_title_style,
            left + AXIS_TRANSLATE - style.y_label_reach,
            plot_h / 2.0,
            -1.0,
            None,
        );
        crate::contract::axis_group_close(&mut svg);
    }
    if style.cell_x_axis {
        svg.push_str(&print(&x_axis.nodes(style.ax_x, left, plot_w, (plot_h, plot_h))?));
    }
    let right_axis = (style.y_right || style.mirror) && !style.own_axis;
    let right_title_extra = if style.y_right { style.y_title_extra } else { 0.0 };
    let value_axis_start = svg.len();
    if own_right || (right_axis && right.paint) {
        let edge_x = left + plot_w + if own_right { 0.0 } else { right.overhang } + AXIS_TRANSLATE;
        crate::contract::axis_group_open(
            &mut svg,
            crate::contract::AxisChannel::Y,
            &if style.y_right { y_caption.clone() } else { y_caption.untitled() },
            (edge_x, 0.0),
        )?;
        crate::common::emit_value_ticks(
            &mut svg,
            &style.y_ticks,
            centers.iter().map(|&(_, cy)| cy),
            edge_x,
            1.0,
            style.y_tick_len,
            &[],
        )?;
        for &(band, cy) in &centers {
            let _ = write!(
                svg,
                "<text x=\"{:.2}\" y=\"{:.2}\" text-anchor=\"{}\" font-family=\"{}\" font-size=\"{}\" \
                 font-weight=\"{}\" fill=\"{}\">{}</text>",
                edge_x + style.y_label_pad,
                cy + label_shift,
                if style.y_right && style.y_own_side { "end" } else { "start" },
                vega_escape_attr(style.y_family),
                style.y_font_size,
                xml_escape_attr(&style.y_weight),
                xml_escape_attr(style.y_color),
                xml_escape(&if style.y_right { shown(band)? } else { ghost_text(band)? })
            );
        }
        if style.y_right {
            crate::common::emit_axis_title_rotated(
                &mut svg,
                style.y_title,
                style.y_title_style,
                edge_x + style.y_label_reach,
                plot_h / 2.0,
                1.0,
                None,
            );
        }
        crate::contract::axis_group_close(&mut svg);
    }
    let value_axis = (right_axis && right.paint)
        .then(|| crate::families::facet_compose::ValueAxisInk { range: value_axis_start..svg.len(), origin: (0.0, 0.0) });
    let svg_end = svg.len();
    svg.push_str("</svg>");

    let mut ink = vega_fit::Bounds::new(left, 0.0, left + plot_w, plot_h).expand(style.marks.reach);
    if right_axis && right.fold {
        ink.union(&vega_fit::Bounds::new(left, 0.0, left + plot_w + right.overhang + style.right_reach() + right_title_extra, plot_h));
    }
    if style.cell_x_axis {
        let mut axis_ink = vega_fit::Bounds::empty_new();
        x_axis.union_ink(&mut axis_ink, plot_w, plot_h);
        ink.union(&vega_fit::Bounds::new(left + axis_ink.x1, axis_ink.y1, left + axis_ink.x2, axis_ink.y2));
    }
    if style.own_axis {
        for &(band, cy) in &centers {
            let b = label_ink(cy, label_w(band));
            ink.union(&vega_fit::Bounds::new(left + b.x1, b.y1, left + b.x2, b.y2));
        }
        if style.y_title_extra > 0.0 {
            let b = own_title_ink(plot_h);
            ink.union(&vega_fit::Bounds::new(left + b.x1, b.y1, left + b.x2, b.y2));
        }
    }
    let geometry = PanelGeometry {
        content_start,
        svg_end,
        top_axis_overhang: (-ink.y1).max(0.0),
        value_title_w: 0.0,
        legend: None,
        ink,
        axis_w_overhang: if right_axis { left + style.right_reach() + right_title_extra } else { left },
        left_mark_overhang: left,
        plot_top: 0.0,
        raw_right_reach: 0.0,
        widest_val_tick_label_width: 0.0,
        val_gutter: 0.0,
        val_tick_size: style.y_tick_len,
        val_tick_marks: if style.own_axis || style.y_tick_len == 0.0 { Vec::new() } else { centers.iter().map(|&(_, cy)| cy).collect() },
        ghost_labels: Vec::new(),
        val_ticks: if style.own_axis {
            Vec::new()
        } else {
            centers.iter().map(|&(band, cy)| Ok((cy + label_shift, if style.y_right { ghost_text(band)? } else { shown(band)? }))).collect::<Result<Vec<_>, String>>()?
        },
        mirror_style: (!style.own_axis).then(|| GhostStyle {
            caption: y_caption.clone(),
            val_font_size: style.y_font_size,
            val_weight: style.y_weight.clone(),
            val_color: style.y_color.to_string(),
            val_family: style.y_family.to_string(),
        }),
        axis: PanelXAxis::Heat(x_axis.clone()),
        value_axis,
    };
    Ok((svg, geometry))
}

/// A gradient legend: a horizontal `length x 16` bar with its ladder labels a
/// `labelOffset` below (end labels flush to the bar's ends), or a vertical bar
/// with its labels beside it.
pub(crate) struct GradientLegend<'a> {
    grad_id: &'a str,
    paint: GradientPaint<'a>,
    values: &'a [f64],
    labels: &'a [String],
    color_min: f64,
    color_max: f64,
    label_offset: f64,
    font_size: f64,
    family: &'a str,
    color: &'a str,
    weight: String,
    legend: &'a LegendConfig,
    /// A vertical gradient (`legend.direction`): a bar beside its labels.
    vertical: bool,
    /// Each ladder label's shaped width (Vega measures them itself).
    label_widths: Vec<f64>,
    max_label_w: f64,
    /// The visible legend title; `None` when untitled.
    title: Option<LegendTitle<'a>>,
}

/// The color a gradient legend's bar runs through.
pub(crate) enum GradientPaint<'a> {
    /// Resolved stops, evenly spaced along the bar.
    Stops(&'a [String]),
    /// A scale sampled at `ticks` (and its two ends).
    Scale(&'a ContinuousColor, Vec<f64>),
}

struct LegendTitle<'a> {
    text: String,
    size: f64,
    family: &'a str,
    color: &'a str,
    weight: String,
    /// Shaped width: Vega sizes the box `ceil(max(title, entries))`.
    width: f64,
}

impl<'a> GradientLegend<'a> {
    pub(crate) fn with_title(mut self, legend: &'a LegendConfig, text: String, fonts: &FontBook) -> Result<Self, String> {
        if text.is_empty() {
            return Ok(self);
        }
        let font = &legend.title.font;
        let size = font.size_or(12.0);
        let family = font.family_or("sans-serif");
        self.title = Some(LegendTitle {
            width: fonts.measure_shaped(&text, family, size)?,
            text,
            size,
            family,
            color: font.color.as_deref().unwrap_or("#313233"),
            weight: font.weight_str(),
        });
        Ok(self)
    }

    /// The bar's length: a vertical one follows the unit's height, clamped; a
    /// horizontal one the unit's width (`horizontal_gradient_length`).
    pub(crate) fn bar_len(&self, unit_w: f64, unit_h: f64) -> f64 {
        if self.vertical {
            crate::families::legend::gradient_length(unit_h)
        } else {
            horizontal_gradient_length(&self.legend.position.edge, self.legend.position.overlay, unit_w)
        }
    }

    /// The box width: a horizontal bar is `bar_len` wide, a vertical one is as
    /// wide as its bar, offset and widest label; the title can widen either.
    pub(crate) fn box_width(&self, bar_len: f64) -> f64 {
        if self.vertical {
            return self.vertical_box(bar_len).0;
        }
        // A label centered on a tick near the bar's far end reaches past it.
        let reach = self
            .values
            .iter()
            .zip(&self.label_widths)
            .map(|(&v, &w)| {
                let frac = unit_position(v, self.color_min, self.color_max);
                if frac >= 1.0 { bar_len } else if frac <= 0.0 { w } else { bar_len * frac + w / 2.0 }
            })
            .fold(bar_len, f64::max);
        let width = self.title.as_ref().map_or(reach, |t| t.width.max(reach));
        if self.title.is_some() || reach > bar_len { width.ceil() } else { width }
    }

    fn vertical_box(&self, bar_len: f64) -> (f64, f64) {
        let (title_w, title_size) = self.title.as_ref().map_or((0.0, 0.0), |t| (t.width, t.size));
        crate::families::legend::gradient_legend_box(title_w, title_size, Some(self.max_label_w), self.label_offset, bar_len)
    }

    /// The legend over `r`'s resolved gradient and label style, its ladder
    /// already formatted into `labels`.
    pub(crate) fn resolve(
        r: &'a HeatmapResolved,
        colors: GradientColors<'a>,
        grad_id: &'a str,
        range: (f64, f64, &'a [f64]),
        labels: &'a [String],
        fonts: &FontBook,
    ) -> Result<Self, String> {
        let paint = match colors {
            GradientColors::Stops(stops) => GradientPaint::Stops(stops),
            GradientColors::Scale(scale) => GradientPaint::Scale(scale, scale.stop_ticks()?),
        };
        Self::over(&r.legend, paint, grad_id, range, labels, fonts).map_err(|e| format!("heatmap: {e}"))
    }

    /// The legend over `legend`'s label style, its bar painted by `paint` and
    /// its ladder already formatted into `labels`.
    pub(crate) fn over(
        legend: &'a LegendConfig,
        paint: GradientPaint<'a>,
        grad_id: &'a str,
        (color_min, color_max, values): (f64, f64, &'a [f64]),
        labels: &'a [String],
        fonts: &FontBook,
    ) -> Result<Self, String> {
        let font = &legend.label.font;
        let label_widths = labels
            .iter()
            .map(|text| fonts.measure_shaped(text, font.family_or("sans-serif"), font.size_or(11.0)))
            .collect::<Result<Vec<_>, _>>()?;
        let max_label_w = label_widths.iter().copied().fold(0.0_f64, f64::max);
        Ok(GradientLegend {
            grad_id,
            paint,
            values,
            labels,
            color_min,
            color_max,
            label_offset: legend.label.padding.ok_or("legend.label.padding absent (theme required)")?,
            font_size: font.size_or(11.0),
            family: font.family_or("sans-serif"),
            color: font.color.as_deref().unwrap_or("#626366"),
            weight: font.weight_str(),
            legend,
            vertical: legend.direction == "vertical",
            label_widths,
            max_label_w,
            title: None,
        })
    }

    /// The bar's `<linearGradient>`: vertical for a legend beside the plot,
    /// horizontal for one above or below it.
    fn def_node(&self, vertical: bool) -> Node {
        match &self.paint {
            GradientPaint::Stops(stops) => stops_gradient_def(self.grad_id, stops, vertical),
            GradientPaint::Scale(color, ticks) => continuous_gradient_def(self.grad_id, color, ticks, vertical),
        }
    }

    /// The box height: a horizontal bar sits over one label row; a vertical
    /// one is `bar_len` tall.
    pub(crate) fn height(&self, bar_len: f64) -> f64 {
        if self.vertical {
            return self.vertical_box(bar_len).1;
        }
        self.title_band() + VEGA_GRADIENT_THICKNESS + self.label_offset + self.font_size
    }

    fn title_band(&self) -> f64 {
        self.title.as_ref().map_or(0.0, |t| t.size + crate::families::legend::VEGA_LEGEND_TITLE_PADDING)
    }

    /// The legend's nodes with the bar `length` long and the group's top-left
    /// at `(x, y)`.
    pub(crate) fn nodes(&self, x: f64, y: f64, length: f64) -> Vec<Node> {
        if self.vertical {
            let bar = VerticalGradient {
                gradient_id: self.grad_id,
                title: self.title.as_ref().map_or("", |t| &t.text),
                range: (self.color_min, self.color_max),
                values: self.values,
                labels: self.labels,
                length,
            };
            let mut nodes = vec![self.def_node(true)];
            nodes.extend(bar.nodes(self.legend, x, y).expect("legend.label.padding is validated by `resolve`"));
            return nodes;
        }
        let mut nodes = Vec::new();
        if let Some(t) = &self.title {
            nodes.push(Node::text(
                Attrs::new()
                    .set("x", format!("{x:.2}"))
                    .set("y", format!("{:.2}", y + baseline_shift("top", t.size)))
                    .set("text-anchor", "start")
                    .vega("font-family", t.family)
                    .set("font-size", t.size.to_string())
                    .set("font-weight", t.weight.clone())
                    .set("fill", t.color),
                t.text.as_str(),
            ));
        }
        let y = y + self.title_band();
        nodes.push(self.def_node(false));
        nodes.push(Node::Rect(
            Attrs::new()
                .set("x", format!("{x:.2}"))
                .set("y", format!("{y:.2}"))
                .set("width", length.to_string())
                .set("height", VEGA_GRADIENT_THICKNESS.to_string())
                .set("fill", format!("url(#{})", self.grad_id)),
        ));
        let label_y = y + VEGA_GRADIENT_THICKNESS + self.label_offset + baseline_shift("top", self.font_size);
        let placed: Vec<(&str, f64)> = self
            .values
            .iter()
            .map(|v| {
                let frac = unit_position(*v, self.color_min, self.color_max);
                let anchor = match frac {
                    f if f <= 0.0 => "start",
                    f if f >= 1.0 => "end",
                    _ => "middle",
                };
                (anchor, x + length * frac)
            })
            .collect();
        // Every label shares one row, so only the horizontal gaps decide an overlap.
        let ink: Vec<vega_fit::Bounds> = placed
            .iter()
            .zip(&self.label_widths)
            .map(|(&(anchor, px), &w)| {
                let left = match anchor {
                    "start" => px,
                    "end" => px - w,
                    _ => px - w / 2.0,
                };
                vega_fit::Bounds::new(left, label_y, left + w, label_y + self.font_size)
            })
            .collect();
        let visible = vega_fit::parity_visible(&ink, 0.0);
        let labels = self
            .labels
            .iter()
            .zip(&placed)
            .zip(visible)
            .map(|((text, &(anchor, px)), shown)| {
                Node::text(
                    Attrs::new()
                        .set("x", format!("{px:.2}"))
                        .set("y", format!("{label_y:.2}"))
                        .set("text-anchor", anchor)
                        .vega("font-family", self.family)
                        .set("font-size", self.font_size.to_string())
                        .set("font-weight", self.weight.clone())
                        .set("fill", self.color)
                        .set_if(!shown, "opacity", "0"),
                    text.as_str(),
                )
            })
            .collect();
        nodes.push(Node::group(contract::legend_label_attrs(None), labels));
        nodes
    }
}

/// What the facet's color channel paints.
enum FacetColor {
    /// A date-like color on a time scale, in day numbers.
    Dates { min: f64, max: f64 },
    Gradient { min: f64, max: f64, ticks: Vec<f64>, scale: Option<ContinuousColor> },
    Categories(CategoryScale),
}

/// A `multiples` heatmap: the composite of one plot per panel.
pub(crate) fn render(payload: &Payload, fonts: &FontBook) -> Result<String, Diagnostic> {
    let folded = fold_wide_y(payload)?;
    let payload = folded.as_ref().unwrap_or(payload);
    let Prelude { r, kind, .. } = Prelude::of(payload)?;
    render_facet(payload, fonts, &r, kind)
}

fn render_facet(
    payload: &Payload,
    fonts: &FontBook,
    r: &HeatmapResolved,
    kind: ColorKind,
) -> Result<String, Diagnostic> {
    let mc = r.multiples.as_ref().expect("render dispatched on multiples");
    let independent = mc.scale == "independent";
    let legend_edge = facet_compose::facet_legend_edge(&r.legend.position);
    let ax_x = &r.style.axis_x;
    let ax_y = &r.style.axis_y;
    if r.panel_axes.is_empty() {
        return Err("heatmap: multiples with no baked panel_axes — resolve() should always supply at least one".into());
    }
    let axes = &r.panel_axes;
    let (rows, x_grain) = date_dimension(&payload.rows, &r.x, &r.style.axis_x, true)?;
    let rows = if r.is_flat() { super::heatmap::with_flat_column(rows) } else { rows };
    // Vega-Lite decides which scales a panel narrows from the data as authored;
    // only the domains it then draws follow the rows with a valid color.
    let authored_panels = multiples::regroup(axes, &rows).map_err(|e| format!("heatmap: {e}"))?;
    let rows = drop_invalid_color_rows(rows, &r.color, kind);
    let panels = multiples::regroup(axes, &rows).map_err(|e| format!("heatmap: {e}"))?;
    let panel_rows_n = multiples::panel_axis_cardinality(axes, mc.rows.as_deref());
    let panel_cols_n = multiples::panel_axis_cardinality(axes, mc.columns.as_deref());
    let missing = multiples::empty_leading_cells(panels.len(), panel_rows_n, panel_cols_n)?;
    let index_of = |field: &Option<String>| field.as_deref().and_then(|f| axes.iter().position(|a| a.field == f));

    // ── Which scales FacetFeature frees per panel ─────────────────────────
    let x_first = first_appearance(&rows, &r.x);
    // `chart.sort` reaches both dimensions identically (`_write_dimension_sort`).
    let sorted = |field: &str, base: Vec<String>| {
        dimension_sort_domain(&rows, field, base, r.sort.as_ref(), SortAggregate::Min, "heatmap")
    };
    let x_domain = sorted(&r.x, x_first)?;
    let y_shared = sorted(&r.y, first_appearance(&rows, &r.y))?;
    if x_domain.is_empty() || y_shared.is_empty() {
        return Err("heatmap: multiples with no x or y values".into());
    }
    // `_domain_subset_narrowing_candidates`: a columns-only facet narrows x
    // per panel, and Vega then draws the x axis in every cell.
    let x_narrowed = mc.rows.is_none() && panel_domain_is_proper_subset(&authored_panels, axes, &r.x);
    let naive_panel_w = multiples::facet_panel_width(payload.width, panel_cols_n, false, 0.0);
    let y_label_family = ax_y.labels.font.family_or("sans-serif");
    let y_label_fs = ax_y.labels.font.size_or(11.0);
    let y_label_limit = ax_y.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    let y_raw_widths: Vec<(String, f64)> = y_shared
        .iter()
        .map(|band| ax_y.labels.measure(fonts, band, y_label_fs).map(|w| (band.clone(), w)))
        .collect::<Result<_, _>>()?;
    let widest_y = y_raw_widths.iter().fold(0.0_f64, |acc, (_, w)| acc.max(*w));
    let y_axis_px = if mc.columns.is_some() { facet_extra_axis_px(&ax_y.labels, &y_shared, fonts)? } else { 0.0 };
    let ghost = r.style.axis_y.mirror_ghost("heatmap")?;
    let mirror = ghost.is_some();
    // The ghost's own format reads each band as the number d3 sees (`gate_label_format`).
    let ghost_texts: Option<std::collections::HashMap<String, String>> = match &ghost {
        Some(g) if g.format.is_some() || g.ruler.is_some() => {
            let values: Vec<f64> = y_shared.iter().map(|band| crate::common::js_number(band)).collect();
            Some(y_shared.iter().cloned().zip(g.texts(&values, &y_shared)?).collect())
        }
        _ => None,
    };
    // `facet_independent_channels`: a mirrored y never narrows, since the ghost
    // layer would be instantiated once per panel.
    let y_subset_narrowed = !mirror
        && panel_domain_is_proper_subset(&authored_panels, axes, &r.y)
        && multiples::extra_axis_is_affordable(naive_panel_w, y_axis_px);
    // An independent scale gives every cell its own y axis, whatever its domain.
    let y_narrowed = independent || y_subset_narrowed;
    let cell_x_axis = x_narrowed;
    let extra_axis_px = if y_subset_narrowed && mc.columns.is_some() { y_axis_px } else { 0.0 };

    // ── Axes and color ───────────────────────────────────────────────────
    let usable_ratio = r
        .style
        .label_usable_ratio
        .ok_or("heatmap: label_usable_ratio absent (theme required for the x axis ladder)")?;
    let crowd_w = multiples::facet_panel_width(payload.width, panel_cols_n, false, extra_axis_px);
    let x_title = crate::common::axis_title_text(&r.x, r.x_label.as_deref(), &r.field_labels)?;
    let (x_box, y_box) = multiples::facet_title_boxes((payload.width, payload.height), (panel_cols_n, panel_rows_n), mirror, extra_axis_px);
    let x_title_paint = crate::common::wrapped_axis_title(&r.id, "x_label", &x_title, &ax_x.title.font, x_box.0, x_box.1, fonts)?;
    let x_axis = HeatmapXAxis::resolve(&x_domain, x_grain, ax_x, crowd_w, usable_ratio, fonts)?.with_title(x_title_paint, &ax_x.title);
    let y_tick_len = ax_y.tick_len();
    let y_own_side = crate::families::heatmap::y_labels_own_side(ax_y, false);
    let y_labels_end = ax_y.position.as_deref() != Some("right") && !y_own_side;
    let widest_label = widest_y.min(y_label_limit);
    // An own-side label grows back across the axis from its baked `labelPadding`; any other hangs
    // away from it, at the theme's padding.
    let y_label_pad = if y_own_side {
        widest_label + y_tick_len + BREATHING_ROOM_PX
    } else {
        y_tick_len + ax_y.labels.padding.ok_or("heatmap: axis_y.labels.padding absent (theme required)")?
    };
    let y_label_reach = if y_own_side { y_label_pad } else { y_label_pad + widest_label };
    let y_title = crate::common::axis_title_text(&r.y, r.y_label.as_deref(), &r.field_labels)?;
    let y_title_paint = crate::common::wrapped_axis_title(&r.id, "y_label", &y_title, &ax_y.title.font, y_box.0, y_box.1, fonts)?;
    let y_title_extra = crate::common::y_title_extra(&ax_y.title, &y_title_paint);
    let y_label_ws: Vec<(String, f64)> = y_raw_widths.iter().map(|(b, w)| (b.clone(), w.min(y_label_limit))).collect();
    let color = match kind {
        ColorKind::Categories => FacetColor::Categories(color_categories(r, &rows)?),
        ColorKind::Dates => {
            let (min, max) = date_color_extent(&rows, &r.color)?;
            FacetColor::Dates { min, max }
        }
        ColorKind::Gradient => {
            let (min, max, ticks, scale) = r.style.color_gradient.ladder(&rows, &r.color, r.legend.visible)?;
            FacetColor::Gradient { min, max, ticks, scale }
        }
    };
    let grad_id = format!("heat_grad-{}", crate::contract::safe_svg_id(&r.id));
    let cells = CellPaint {
        x_field: &r.x,
        y_field: &r.y,
        color_field: &r.color,
        fill: match &color {
            FacetColor::Gradient { min, max, scale, .. } => CellFill::Gradient {
                colors: GradientColors::new(&r.style.color_gradient, scale.as_ref()),
                min: *min,
                max: *max,
            },
            FacetColor::Categories(scale) => CellFill::Categories(scale),
            FacetColor::Dates { min, max } => CellFill::Dates { min: *min, max: *max },
        },
        tooltip: tooltip_value_title(r)?.zip(r.style.tooltip_format.as_ref()),
        native_aria: false,
        x_header: HeaderFmt::of(&rows, &r.x, ax_x)?,
        y_header: HeaderFmt::of(&rows, &r.y, ax_y)?,
        link: r.datum_link((x_grain.is_some(), false, kind == ColorKind::Dates)),
        mirror,
        ghost_titles: Some((x_title.as_str(), y_title.as_str())),
    };
    let style = PanelStyle {
        ax_x,
        ax_y,
        cell_x_axis,
        cells,
        marks: CellMarks::of(&r.style.rect_mark)?,
        y_font_size: y_label_fs,
        y_family: y_label_family,
        y_color: ax_y.labels.font.color.as_deref().unwrap_or("#626366"),
        y_weight: ax_y.labels.font.weight_str(),
        y_label_ws: &y_label_ws,
        y_label_pad,
        y_label_reach,
        y_labels_end,
        y_tick_len,
        own_axis: y_narrowed,
        y_right: ax_y.position.as_deref() == Some("right"),
        y_own_side,
        y_ticks: &ax_y.ticks,
        y_title: &y_title_paint,
        y_title_style: &ax_y.title,
        y_title_extra,
        mirror,
        ghost_texts: ghost_texts.as_ref(),
    };

    // A partition field the chart also encodes stays a column of its own
    // panel's rows in Vega-Lite; `regroup` strips it, so it is restored.
    let panel_rows: Vec<Vec<Map<String, Value>>> = panels
        .iter()
        .map(|panel| {
            panel
                .rows
                .iter()
                .map(|row| {
                    let mut row = row.clone();
                    for (axis, value) in axes.iter().zip(&panel.key) {
                        row.insert(axis.field.clone(), value.clone());
                    }
                    row
                })
                .collect()
        })
        .collect();
    let panel_y_domains: Vec<Vec<String>> = panel_rows
        .iter()
        .map(|rows| if y_narrowed { narrowed_domain(rows, &r.y, r.sort.as_ref()) } else { Ok(y_shared.clone()) })
        .collect::<Result<_, String>>()?;
    // A narrowed x gives every panel the bands it holds, in its own order.
    let panel_x: Vec<(Vec<String>, HeatmapXAxis)> = panel_rows
        .iter()
        .map(|rows| {
            let domain = if x_narrowed {
                narrowed_domain(rows, &r.x, r.sort.as_ref())?
            } else {
                x_domain.clone()
            };
            let axis = if x_narrowed {
                x_axis.restricted_to(&domain)
            } else {
                x_axis.clone()
            };
            Ok((domain, axis))
        })
        .collect::<Result<_, String>>()?;
    let header_font_family = r.legend.label.font.family.clone().unwrap_or_else(|| "sans-serif".into());

    // ── Chrome above the grid ─────────────────────────────────────────────
    let pad = payload.padding.as_ref();
    let avail_w = payload.width - pad.map_or(0.0, |p| p.left + p.right);
    let title_src = TitleSource {
        family: "heatmap",
        title: r.title.as_deref(),
        subtitle: r.subtitle.as_deref(),
        title_font: r.style.title_font.as_ref(),
        title_style: r.title_style.as_ref(),
    };
    let title_plan = match layout_title(&title_src, fonts, avail_w)? {
        TitleLayout::Plan(plan) => {
            plan.warn_truncated(&r.id);
            Some(plan)
        }
        TitleLayout::Untitled => None,
    };
    let legend_title = crate::families::legend::legend_title_text(&r.color, &r.legend, &r.field_labels)?;
    let leg_labels: Vec<String> = match &color {
        FacetColor::Gradient { ticks, scale, .. } => {
            legend_labels(ticks, r.style.tooltip_format.as_ref().map(|f| f.spec.as_str()), scale.as_ref())?
        }
        FacetColor::Categories(_) | FacetColor::Dates { .. } => Vec::new(),
    };
    // The mirror's ghost rule layer strokes the same color, which turns the
    // gradient into a symbol legend: a circle per ladder rung.
    let ladder_symbols = style.mirror && kind != ColorKind::Categories;
    let legend = match &color {
        FacetColor::Gradient { min, max, ticks, scale } if !ladder_symbols => r
            .legend
            .visible
            .then(|| {
                let colors = GradientColors::new(&r.style.color_gradient, scale.as_ref());
                GradientLegend::resolve(r, colors, &grad_id, (*min, *max, ticks), &leg_labels, fonts)?
                    .with_title(&r.legend, legend_title.clone(), fonts)
            })
            .transpose()?,
        _ => None,
    };
    let ladder: Vec<(String, String)> = match &color {
        FacetColor::Gradient { min, max, ticks, scale } if ladder_symbols => leg_labels
            .iter()
            .zip(ticks)
            .map(|(label, &tick)| Ok((label.clone(), GradientColors::new(&r.style.color_gradient, scale.as_ref()).at(*min, *max, tick)?)))
            .collect::<Result<_, String>>()?,
        FacetColor::Dates { min, max } if ladder_symbols => {
            date_legend_ticks(*min, *max, SYMBOL_LEGEND_TICK_COUNT, r.legend.values.as_deref())?
                .into_iter()
                .map(|day| Ok((legend_date_label(day)?, date_color(*min, *max, day))))
                .collect::<Result<_, String>>()?
        }
        _ => Vec::new(),
    };
    let symbol_entries: Vec<LegendEntry> = match &color {
        FacetColor::Categories(scale) => category_legend_entries(scale, r.legend.values.as_deref()),
        FacetColor::Gradient { .. } | FacetColor::Dates { .. } => ladder
            .iter()
            .map(|(label, fill)| LegendEntry { label, fill, symbol: None, series_key: None, recede: None })
            .collect(),
    };
    // A time-scale bar's ladder follows its drawn length, so it is measured and
    // painted per unit; probing both length extremes here raises its errors once.
    let dates = match &color {
        FacetColor::Dates { min, max } if r.legend.visible && !ladder_symbols => Some(crate::families::heatmap::date_scale(*min, *max)),
        _ => None,
    };
    if let Some(scale) = &dates {
        for unit in [(0.0, 0.0), (f64::MAX, f64::MAX)] {
            GradientAt::measure(&r.legend, None, scale, (&grad_id, &legend_title), fonts, unit)?;
        }
    }
    let entry_symbol = if ladder_symbols { LegendSymbol::Circle } else { LegendSymbol::FilledSquare };
    let symbol_legend = (r.legend.visible && (kind == ColorKind::Categories || ladder_symbols))
        .then(|| vega_legend(&symbol_entries, false, &legend_title, &r.legend, fonts))
        .transpose()?;
    let render_pass = |plot_w: f64, plot_h: f64, value_axis_right_overhang: f64| -> Result<_, Diagnostic> {
        Ok(panel_rows
            .iter()
            .zip(&panel_y_domains)
            .zip(&panel_x)
            .enumerate()
            .map(|(i, ((rows, y_domain), (x_domain, x_axis)))| {
                let right = RightAxis {
                    paint: (missing + i) % panel_cols_n == panel_cols_n - 1,
                    fold: panel_cols_n == 1,
                    overhang: value_axis_right_overhang,
                };
                render_panel(&style, rows, (x_domain, x_axis), y_domain, plot_w, plot_h, &right)
            })
            .collect::<Result<Vec<_>, String>>()?)
    };
    let chrome = FacetChrome {
        title: title_plan.as_ref().map(|plan| TitleChrome {
            ink: plan.ink.clone(),
            paint: Box::new(|svg, x, y| plan.paint(&title_src, svg, x, y)),
        }),
        legends: legend
            .as_ref()
            .map(|legend| LegendChrome {
                edge: legend_edge,
                width: Box::new(|panel_w, panel_h| legend.box_width(legend.bar_len(panel_w, panel_h))),
                height: Box::new(|panel_h| legend.height(legend.bar_len(0.0, panel_h))),
                paint: Box::new(|svg, x, y, panel_w, panel_h| svg.push_str(&print(&legend.nodes(x, y, legend.bar_len(panel_w, panel_h))))),
            })
            .into_iter()
            .chain(dates.as_ref().map(|scale| LegendChrome {
                edge: legend_edge,
                width: Box::new(|panel_w, panel_h| {
                    GradientAt::measure(&r.legend, None, scale, (&grad_id, &legend_title), fonts, (panel_w, panel_h))
                        .expect("probed at both length extremes")
                        .0
                }),
                height: Box::new(|panel_h| {
                    GradientAt::measure(&r.legend, None, scale, (&grad_id, &legend_title), fonts, (0.0, panel_h))
                        .expect("probed at both length extremes")
                        .1
                }),
                paint: Box::new(|svg, x, y, panel_w, panel_h| {
                    GradientAt::paint(&r.legend, None, scale, (&grad_id, &legend_title), fonts, (panel_w, panel_h), (svg, x, y))
                        .expect("probed at both length extremes");
                }),
            }))
            .chain(symbol_legend.as_ref().map(|symbols| LegendChrome {
                edge: legend_edge,
                width: Box::new(|_, _| symbols.box_w),
                height: Box::new(|_| symbols.box_h()),
                paint: Box::new(|svg, x, y, _, _| {
                    emit_vega_legend(svg, &symbol_entries, symbols, &r.legend, entry_symbol, x, y, false)
                }),
            }))
            .collect(),
    };
    let spec = FacetSpec {
        family: "heatmap",
        panels: &panels,
        axes,
        panel_rows_n,
        panel_cols_n,
        row_axis_idx: index_of(&mc.rows),
        col_axis_idx: index_of(&mc.columns),
        has_mirror: style.mirror,
        left_axis_only: false,
        left_title: None,
        mirror_in_panel: true,
        own_value_axes: independent,
        y_axes_in_cells: independent || y_narrowed,
        extra_axis_px,
        cell_x_axis,
        axis_x: ax_x,
        axis_y: &r.style.axis_y,
        header_font_family: &header_font_family,
        background: r.background.as_str(),
        x_scale: crate::ViewScale::Discrete,
    };
    let row_axis = (!y_narrowed && ax_y.position.as_deref() != Some("right")).then_some(RowAxisSpec {
        reserve: y_label_reach + y_title_extra,
        label_x: AXIS_TRANSLATE - y_label_pad,
        label_anchor: if y_labels_end { "end" } else { "start" },
        title_x: AXIS_TRANSLATE - y_label_reach,
        title: &y_title_paint,
        title_style: &ax_y.title,
        hugs_labels: None,
    });
    facet_compose::compose_facet_full(payload, fonts, &spec, Some(&chrome), row_axis.as_ref(), &render_pass)
}

#[cfg(test)]
mod tests {
    use super::{facet_extra_axis_px, narrowed_domain, AxisLabels, ChartSort, CHROME_PX};
    use measure::FontBook;
    use serde_json::{Map, Value};

    fn labels_at(weight: &str, max_width: Option<f64>) -> AxisLabels {
        serde_json::from_value(serde_json::json!({
            "font": {"family": "'Inter Variable', Inter", "size": 11.0, "weight": weight},
            "max_width": max_width,
        }))
        .expect("axis labels parse")
    }

    fn shipped_fonts() -> FontBook {
        FontBook::load_dir(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../src/dbt_charts/core/render/fonts"
        ))
        .expect("shipped fonts load")
    }

    /// A narrowed panel is Vega's own sort over its rows: the bands it holds,
    /// ranked by the `min` of the sort field in that panel alone.
    #[test]
    fn a_narrowed_panel_ranks_its_bands_by_its_own_rows() {
        let rows: Vec<Map<String, Value>> = [("a", 5.0), ("b", 1.0), ("c", 9.0), ("a", 2.0)]
            .iter()
            .map(|(x, v)| serde_json::json!({"x": x, "v": v}).as_object().unwrap().clone())
            .collect();
        let sort = |order| ChartSort { by: "v".into(), order };
        assert_eq!(narrowed_domain(&rows, "x", None).unwrap(), ["a", "b", "c"]);
        assert_eq!(narrowed_domain(&rows, "x", Some(&sort(crate::common::SortDir::Asc))).unwrap(), ["b", "a", "c"]);
        assert_eq!(narrowed_domain(&rows, "x", Some(&sort(crate::common::SortDir::Desc))).unwrap(), ["c", "a", "b"]);
    }

    /// `facet_extra_axis_width_px` (`_cartesian.py:1151`) sizes the extra axis
    /// with the labels' painted weight, and clamps the widest at the label limit.
    #[test]
    fn the_extra_axis_costs_more_when_its_labels_paint_bold() {
        let fonts = shipped_fonts();
        let bands = ["Northeast Territory".to_string(), "Southwest".to_string()];
        let regular = facet_extra_axis_px(&labels_at("400", None), &bands, &fonts).unwrap();
        let bold = facet_extra_axis_px(&labels_at("800", None), &bands, &fonts).unwrap();
        assert!(bold - regular > 1.0, "{regular} vs {bold}");
        assert_eq!(facet_extra_axis_px(&labels_at("semibold", None), &bands, &fonts).unwrap(), regular);
        assert_eq!(facet_extra_axis_px(&labels_at("800", Some(40.0)), &bands, &fonts).unwrap(), CHROME_PX + 40.0);
    }

    /// The extra axis budgets the cased text it paints: capitals run wider than lowercase.
    #[test]
    fn the_extra_axis_costs_what_the_cased_labels_measure() {
        let fonts = shipped_fonts();
        let bands = ["Northeast Territory".to_string(), "Southwest".to_string()];
        let at = |case: &str| {
            let mut labels = labels_at("400", None);
            labels.font.case = Some(case.into());
            facet_extra_axis_px(&labels, &bands, &fonts).unwrap()
        };
        assert!(at("upper") > at("none") && at("none") > at("lower"), "{} {} {}", at("upper"), at("none"), at("lower"));
    }
}
