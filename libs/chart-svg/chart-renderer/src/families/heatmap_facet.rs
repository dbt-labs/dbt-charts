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
use crate::constants::layout::FACET_MIN_PANEL_PX;
use std::collections::HashSet;
use std::fmt::Write as _;

use serde_json::{Map, Value};

use crate::common::{dimension_sort_domain, row_str, AxisLabels, AxisStyle, ChartSort, SortAggregate};
use crate::families::bar::CHROME_PX;
use crate::families::cartesian::BandScale;
use crate::families::facet_compose::{
    self, FacetChrome, FacetSpec, GhostStyle, LegendChrome, PanelGeometry, PanelXAxis, RowAxisSpec, TitleChrome,
};
use crate::families::facet_title::{layout_title, TitleLayout, TitleSource};
use crate::families::heatmap::{
    category_legend_entries, color_categories, interp_color, emit_gradient_def, CategoryScale, emit_rotated_x_label, format_legend_value, svg_text_anchor, gradient_ladder, paint_cells, tooltip_value_title, CellFill, CellPaint,
    HeatmapResolved, AXIS_TRANSLATE, BREATHING_ROOM_PX,
};
use crate::families::color_scale::ContinuousColor;
use crate::families::legend::{
    LegendEntry, emit_vega_legend, horizontal_gradient_length, unit_position, vega_legend, LegendConfig, LegendSymbol,
    VEGA_GRADIENT_THICKNESS,
};
use crate::families::multiples::{self, Panel};
use crate::families::temporal::{
    axis_time_format, infer_vega_type, normalize_labeled_temporal, ordinal_date_labels, parse_iso_date, AuthoredTimeFormat,
    ExprBands, LabelUnit, TemporalLabel, TimeUnit, VegaType,
};
use crate::families::x_type::{parse_date, resolve_cartesian_x_type, Grain, XAxis};
use crate::svg::{vega_escape_attr, xml_escape, xml_escape_attr};
use crate::text::FontBook;
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
    /// What a visible title adds below the label ink (`x_title_extra`); `0.0` untitled.
    title_extra: f64,
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
        let texts = band_texts(domain, labels_cfg.expr.as_deref())?;
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
        let refs: Vec<&str> = domain.iter().map(String::as_str).collect();
        if let Some(cell) = refs.iter().find(|d| parse_iso_date(d).is_none()) {
            return Err(format!("heatmap: a date x holds {cell:?}, which is not an ISO date"));
        }
        let labels_cfg = &ax_x.labels;
        let unit = LabelUnit::resolve(data_tu, labels_cfg.time_unit.as_deref()).map_err(|e| format!("heatmap: {e}"))?;
        let authored = AuthoredTimeFormat::parse(
            "heatmap",
            axis_time_format(labels_cfg.spec(), &ax_x.grain_formats, unit.grain()),
        )?;
        let font_size = labels_cfg.font.size_or(11.0);
        let mut measure = |s: &str| labels_cfg.font.measure_weighted(fonts, s, font_size);
        let expr_n = labels_cfg
            .expr
            .as_deref()
            .map(|expr| crate::common::parse_labels_expr_slice_n(expr).map_err(|e| format!("heatmap: {e}")))
            .transpose()?;
        // A heatmap collapses `values` to the ticks the ladder keeps.
        let resolved = ordinal_date_labels(
            domain,
            data_tu,
            unit,
            labels_cfg,
            ax_x.fiscal_year_start_month,
            authored,
            crowd_w * usable_ratio,
            crowd_w,
            Some(usable_ratio),
            expr_n,
            ExprBands::KeptByLadder,
            &mut measure,
            font_size,
        )
        .map_err(|e| format!("heatmap: {e}"))?;
        let angle = labels_cfg.angle.or(resolved.angle);
        let parity = resolved.parity;
        let temporal = resolved.labels;
        let measure = Self::measurer(ax_x, fonts);
        let labels = temporal
            .into_iter()
            .map(|TemporalLabel { domain_idx, row1, row2 }| AxisLabel::measured(domain_idx, row1, row2, &measure))
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
            title_extra: 0.0,
        })
    }

    pub(crate) fn with_title(mut self, title: String, style: &crate::common::AxisTitle) -> Self {
        self.title_extra = crate::common::x_title_extra(style, &title);
        self.title = title;
        self
    }

    /// One label's ink in the axis frame, below a plot whose bottom edge is at
    /// `row_y - offset - tick_len - label_pad`: a rotated block turns about its
    /// anchor, a flat one is centered on its band. A second row hangs a line
    /// advance below the first.
    fn label_ink(&self, scale: &BandScale, row_y: f64, label: &AxisLabel) -> vega_fit::Bounds {
        let center = scale.position(&self.domain[label.domain_idx]).expect("a label names a band of its own domain")
            + scale.bandwidth() / 2.0;
        let (align, baseline) =
            if self.angle == 0.0 { ("center", "top") } else { vega_fit::vl_x_label_anchor(self.angle, true) };
        crate::common::rotated_category_label_ink(
            center,
            row_y,
            label.w1.max(label.w2),
            self.font_size,
            align,
            baseline,
            self.angle,
            !label.row2.is_empty(),
        )
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
        plot_h + self.offset + self.tick_len + self.label_pad
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
        let reach = self.labels.iter().fold(row_y, |acc, label| acc.max(self.label_ink(&scale, row_y, label).y2));
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
            return self.labels.iter().fold(0.0, |acc, label| acc.max(self.label_ink(&scale, 0.0, label).y2));
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
            b.union(&vega_fit::Bounds::new(plot_w / 2.0, b.y2, plot_w / 2.0, b.y2 + self.title_extra));
        }
    }

    fn union_label_ink(&self, b: &mut vega_fit::Bounds, plot_w: f64, plot_h: f64) {
        let scale = BandScale::new(self.domain.clone(), 0.0, plot_w, 0.0, 0.0);
        if self.labels_hidden {
            let tick_bottom = plot_h + self.offset + self.tick_len;
            for label in &self.labels {
                let x = scale.position(&self.domain[label.domain_idx]).expect("a band of its own domain")
                    + scale.bandwidth() / 2.0;
                b.union(&vega_fit::Bounds::new(x.floor() + AXIS_TRANSLATE, plot_h + self.offset, x.floor() + AXIS_TRANSLATE, tick_bottom));
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

    /// Paints at the CALLER's `plot_left`/`plot_w`/`plot_bottom`. The axis
    /// group sits `offset` plus Vega's half-pixel crisp translate below the
    /// plot; a tick snaps to a whole pixel (`floor(center) + 0.5`) while its
    /// label keeps the exact band center.
    pub(crate) fn paint(&self, svg: &mut String, ax_x: &AxisStyle, plot_left: f64, plot_w: f64, plot_bottom: f64) -> Result<(), String> {
        let scale = BandScale::new(self.domain.clone(), 0.0, plot_w, 0.0, 0.0);
        let axis_top = plot_bottom + self.offset + AXIS_TRANSLATE;
        let label_y = axis_top + self.tick_len + self.label_pad + vega_fit::baseline_shift("top", self.font_size);
        let row2_y = label_y + vega_fit::label_row_advance(self.font_size);
        let tick_color = crate::common::required_ink(ax_x.ticks.color.as_deref(), "heatmap_facet", "axis_x.ticks.color")?;
        let tick_width = ax_x.ticks.width.unwrap_or(1.0);
        let label_color = ax_x.labels.font.color.as_deref().unwrap_or("#626366");
        let label_weight = ax_x.labels.font.weight_str();
        crate::contract::axis_group_open(svg, crate::contract::AxisChannel::X, (plot_left + AXIS_TRANSLATE, axis_top))?;
        let (align, baseline) =
            if self.angle == 0.0 { ("center", "top") } else { vega_fit::vl_x_label_anchor(self.angle, true) };
        let anchor_y = axis_top + self.tick_len + self.label_pad;
        let dy = vega_fit::baseline_shift(baseline, self.font_size);
        let text_anchor = svg_text_anchor(align);
        let visible = self.visible(plot_w);
        for (label, &shown) in self.labels.iter().zip(&visible) {
            let center = scale.position(&self.domain[label.domain_idx]).expect("a label names a band of its own domain")
                + scale.bandwidth() / 2.0;
            if self.tick_len > 0.0 {
                let tick_x = plot_left + center.floor() + AXIS_TRANSLATE;
                let _ = write!(
                    svg,
                    "<line x1=\"{tick_x:.2}\" y1=\"{axis_top:.2}\" x2=\"{tick_x:.2}\" y2=\"{:.2}\" \
                     stroke=\"{}\" stroke-width=\"{tick_width}\"/>",
                    axis_top + self.tick_len,
                    xml_escape_attr(tick_color)
                );
            }
            if self.labels_hidden {
                continue;
            }
            let opacity = if shown { "" } else { " opacity=\"0\"" };
            if self.angle != 0.0 {
                emit_rotated_x_label(
                    svg,
                    (plot_left + center, anchor_y, self.angle, dy),
                    (text_anchor, &self.family, self.font_size, &label_weight, label_color),
                    (&label.row1, &label.row2),
                    opacity,
                );
                continue;
            }
            for (text, y) in [(&label.row1, label_y), (&label.row2, row2_y)] {
                if text.is_empty() {
                    continue;
                }
                let _ = write!(
                    svg,
                    "<text x=\"{:.2}\" y=\"{y:.2}\" text-anchor=\"middle\" font-family=\"{}\" \
                     font-size=\"{}\" font-weight=\"{}\" fill=\"{}\"{opacity}>{}</text>",
                    plot_left + center,
                    vega_escape_attr(&self.family),
                    self.font_size,
                    xml_escape_attr(&label_weight),
                    xml_escape_attr(label_color),
                    xml_escape(text)
                );
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
            let title_y = axis_top + bounds_bottom + ax_x.title.padding.unwrap_or(10.0) + vega_fit::baseline_shift("top", title_fs);
            crate::common::emit_axis_title_at(svg, &self.title, &ax_x.title, plot_left + plot_w / 2.0, title_y, None);
            crate::contract::axis_group_close(svg);
            return Ok(());
        }
        if crate::common::axis_title_band(&ax_x.title, &self.title) > 0.0 {
            let rows_offset = if self.labels.iter().any(|l| !l.row2.is_empty()) {
                vega_fit::label_row_advance(self.font_size)
            } else {
                0.0
            };
            crate::common::emit_axis_title_bottom(
                svg,
                &self.title,
                &ax_x.title,
                plot_left + plot_w / 2.0,
                label_y + rows_offset,
                self.font_size,
                None,
            );
        }
        crate::contract::axis_group_close(svg);
        Ok(())
    }
}

/// What each band paints: the band itself, or an authored `labels.expr`'s
/// rewrite of it (`labelExpr` runs on `datum.value`, the raw band).
fn band_texts(domain: &[String], expr: Option<&str>) -> Result<Vec<String>, String> {
    let Some(expr) = expr else { return Ok(domain.to_vec()) };
    let n = crate::common::parse_labels_expr_slice_n(expr).map_err(|e| format!("heatmap: {e}"))?;
    Ok(domain.iter().map(|d| crate::common::apply_labels_expr(d, n)).collect())
}

/// The width one extra per-column-panel y axis would cost
/// (`facet_extra_axis_width_px`, `_cartesian.py:1081-1156`): the chrome plus the
/// widest band label, clamped to the label limit. The labels are measured at the
/// weight they paint at (`:1151`), so a bold axis costs more than a regular one.
pub(crate) fn facet_extra_axis_px(labels: &AxisLabels, bands: &[String], fonts: &FontBook) -> Result<f64, String> {
    let size = labels.font.size_or(11.0);
    let limit = labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    let widest = bands.iter().try_fold(0.0_f64, |acc, band| {
        labels.font.measure_weighted(fonts, band, size).map(|w| acc.max(w))
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
    let normalized = if is_x { normalize_labeled_temporal(rows, field)? } else { rows.to_vec() };
    let resolution = law(&normalized)?;
    let Grain::Calendar(tu) = resolution.grain else { return Ok((normalized, None)) };
    if !matches!(resolution.from_data, VegaType::Temporal | VegaType::Ordinal) {
        return Ok((normalized, None));
    }
    let mut rows = if is_x { normalized } else { normalize_labeled_temporal(&normalized, field)? };
    if infer_vega_type(&rows, field) == VegaType::Temporal {
        for row in &mut rows {
            if let Some(((y, m, d), _)) = row.get(field).and_then(Value::as_str).and_then(parse_date) {
                row.insert(field.to_string(), Value::String(format!("{y:04}-{m:02}-{d:02}")));
            }
        }
        if is_x {
            rows.sort_by_key(|row| row_str(row, field));
        }
    }
    Ok((rows, Some(tu)))
}

/// Everything a panel paint reads that is common to the whole composite.
struct PanelStyle<'a> {
    ax_x: &'a AxisStyle,
    /// Every panel draws its own x axis inside its cell.
    cell_x_axis: bool,
    cells: CellPaint<'a>,
    y_font_size: f64,
    y_family: &'a str,
    y_color: &'a str,
    y_weight: String,
    y_label_ws: &'a [(String, f64)],
    /// `labelPadding`: where a left-anchored label starts, left of the plot.
    y_label_pad: f64,
    y_tick_len: f64,
    /// A narrowed y draws its axis inside every cell; a shared one is the
    /// composer's row-header axis.
    own_axis: bool,
    /// `axis_y.position: right`: the shared axis is a `row_footer` at the last
    /// column of each row rather than the composer's left `row_header`.
    y_right: bool,
    y_ticks: &'a crate::common::AxisTicks,
    y_title: &'a str,
    y_title_style: &'a crate::common::AxisTitle,
    /// What a visible y title adds left of the label band (`y_title_extra`).
    y_title_extra: f64,
    /// `axis_y.mirror`: the shared axis stays on the left and its labels repeat
    /// on the right edge (a `row_footer` without the title), starting at the
    /// primary's own `labelPadding` (`y_label_pad`).
    mirror: bool,
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
    let label_w = |band: &str| {
        style.y_label_ws.iter().find(|(label, _)| label == band).map(|(_, w)| *w).expect("label measured")
    };
    // A cell's own axis sits on the left of its plot, or on the right when
    // `axis_y.position` says so, where the labels start past the tick.
    let own_right = style.own_axis && style.y_right;
    let label_x = if own_right {
        plot_w + AXIS_TRANSLATE + style.y_tick_len + BREATHING_ROOM_PX
    } else {
        AXIS_TRANSLATE - style.y_label_pad
    };
    let centers: Vec<(&String, f64)> = y_domain
        .iter()
        .map(|band| (band, y_scale.position(band).expect("band in its own domain") + y_scale.bandwidth() / 2.0))
        .collect();
    let label_ink = |cy: f64, w: f64| vega_fit::text_ink(label_x, cy, w, style.y_font_size, "left", "middle");
    let own_title_ink = |plot_h: f64| {
        if own_right {
            let edge = plot_w + AXIS_TRANSLATE + style.y_label_pad;
            vega_fit::Bounds::new(edge, 0.0, edge + style.y_title_extra, plot_h)
        } else {
            vega_fit::Bounds::new(label_x - style.y_title_extra, 0.0, label_x, plot_h)
        }
    };
    let margins = vega_fit::pad_plot(plot_w, plot_h, |_, _, b| {
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
    paint_cells(&mut svg, rows, &x_scale, &y_scale, &style.cells)?;
    svg.push_str("</g>");
    let label_shift = vega_fit::baseline_shift("middle", style.y_font_size);
    if style.own_axis && !own_right {
        crate::contract::axis_group_open(&mut svg, crate::contract::AxisChannel::Y, (left + label_x, 0.0))?;
        crate::common::emit_value_ticks(
            &mut svg,
            &style.y_ticks,
            centers.iter().map(|&(_, cy)| cy),
            left + AXIS_TRANSLATE,
            -1.0,
            style.y_tick_len,
        )?;
        for &(band, cy) in &centers {
            let _ = write!(
                svg,
                "<text x=\"{:.2}\" y=\"{:.2}\" text-anchor=\"start\" font-family=\"{}\" font-size=\"{}\" \
                 font-weight=\"{}\" fill=\"{}\">{}</text>",
                left + label_x,
                cy + label_shift,
                vega_escape_attr(style.y_family),
                style.y_font_size,
                xml_escape_attr(&style.y_weight),
                xml_escape_attr(style.y_color),
                xml_escape(band)
            );
        }
        crate::common::emit_axis_title_rotated(
            &mut svg,
            style.y_title,
            style.y_title_style,
            left + label_x,
            plot_h / 2.0,
            -1.0,
            None,
        );
        crate::contract::axis_group_close(&mut svg);
    }
    if style.cell_x_axis {
        x_axis.paint(&mut svg, style.ax_x, left, plot_w, plot_h)?;
    }
    let right_axis = (style.y_right || style.mirror) && !style.own_axis;
    let right_title_extra = if style.y_right { style.y_title_extra } else { 0.0 };
    let value_axis_start = svg.len();
    if own_right || (right_axis && right.paint) {
        let edge_x = left + plot_w + if own_right { 0.0 } else { right.overhang } + AXIS_TRANSLATE;
        crate::contract::axis_group_open(&mut svg, crate::contract::AxisChannel::Y, (edge_x, 0.0))?;
        crate::common::emit_value_ticks(
            &mut svg,
            &style.y_ticks,
            centers.iter().map(|&(_, cy)| cy),
            edge_x,
            1.0,
            style.y_tick_len,
        )?;
        for &(band, cy) in &centers {
            let _ = write!(
                svg,
                "<text x=\"{:.2}\" y=\"{:.2}\" text-anchor=\"start\" font-family=\"{}\" font-size=\"{}\" \
                 font-weight=\"{}\" fill=\"{}\">{}</text>",
                edge_x + if style.y_right { style.y_tick_len + BREATHING_ROOM_PX } else { style.y_label_pad },
                cy + label_shift,
                vega_escape_attr(style.y_family),
                style.y_font_size,
                xml_escape_attr(&style.y_weight),
                xml_escape_attr(style.y_color),
                xml_escape(band)
            );
        }
        if style.y_right {
            crate::common::emit_axis_title_rotated(
                &mut svg,
                style.y_title,
                style.y_title_style,
                edge_x + style.y_label_pad,
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

    let mut ink = vega_fit::Bounds::new(left, 0.0, left + plot_w, plot_h);
    if right_axis && right.fold {
        ink.union(&vega_fit::Bounds::new(left, 0.0, left + plot_w + right.overhang + style.y_label_pad + right_title_extra, plot_h));
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
        legend: None,
        ink,
        axis_w_overhang: if right_axis { left + style.y_label_pad + right_title_extra } else { left },
        left_mark_overhang: left,
        plot_top: 0.0,
        raw_right_reach: 0.0,
        widest_val_tick_label_width: 0.0,
        val_gutter: 0.0,
        val_tick_size: style.y_tick_len,
        val_tick_marks: if style.own_axis || style.y_tick_len == 0.0 { Vec::new() } else { centers.iter().map(|&(_, cy)| cy).collect() },
        val_ticks: if style.own_axis {
            Vec::new()
        } else {
            centers.iter().map(|&(band, cy)| (cy + label_shift, band.clone())).collect()
        },
        mirror_style: (!style.own_axis).then(|| GhostStyle {
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
    /// The widest ladder label, shaped (Vega measures it itself).
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
        let width = self.title.as_ref().map_or(bar_len, |t| t.width.max(bar_len));
        if self.title.is_some() { width.ceil() } else { width }
    }

    fn vertical_box(&self, bar_len: f64) -> (f64, f64) {
        let (title_w, title_size) = self.title.as_ref().map_or((0.0, 0.0), |t| (t.width, t.size));
        crate::families::legend::gradient_legend_box(title_w, title_size, Some(self.max_label_w), self.label_offset, bar_len)
    }

    /// The legend over `r`'s resolved gradient and label style, its ladder
    /// already formatted into `labels`.
    pub(crate) fn resolve(
        r: &'a HeatmapResolved,
        grad_id: &'a str,
        range: (f64, f64, &'a [f64]),
        labels: &'a [String],
        fonts: &FontBook,
    ) -> Result<Self, String> {
        let paint = GradientPaint::Stops(&r.style.color_gradient.resolved_stops);
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
        let max_label_w = labels.iter().try_fold(0.0_f64, |acc, text| {
            fonts.measure_shaped(text, font.family_or("sans-serif"), font.size_or(11.0)).map(|w| acc.max(w))
        })?;
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
            max_label_w,
            title: None,
        })
    }

    /// The bar's `<linearGradient>`: vertical for a legend beside the plot,
    /// horizontal for one above or below it.
    fn emit_def(&self, svg: &mut String, vertical: bool) {
        match &self.paint {
            GradientPaint::Stops(stops) => emit_gradient_def(svg, self.grad_id, stops, vertical),
            GradientPaint::Scale(color, _) if color.lo == color.hi => {
                crate::families::legend::emit_ramp_gradient_def(svg, self.grad_id, vertical, |t| color.fill_at(t));
            }
            GradientPaint::Scale(color, ticks) => crate::families::legend::emit_scale_gradient_def(
                svg,
                self.grad_id,
                (color.lo, color.hi),
                ticks,
                |v| color.fill(v),
                vertical,
            ),
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

    pub(crate) fn paint(&self, svg: &mut String, x: f64, y: f64, length: f64) {
        if self.vertical {
            self.emit_def(svg, true);
            crate::families::legend::VerticalGradient {
                gradient_id: self.grad_id,
                title: self.title.as_ref().map_or("", |t| &t.text),
                range: (self.color_min, self.color_max),
                values: self.values,
                labels: self.labels,
                length,
            }
            .paint(svg, self.legend, x, y)
            .expect("legend.label.padding is validated by `resolve`");
            return;
        }
        if let Some(t) = &self.title {
            let _ = write!(
                svg,
                "<text x=\"{x:.2}\" y=\"{:.2}\" text-anchor=\"start\" font-family=\"{}\" font-size=\"{}\" \
                 font-weight=\"{}\" fill=\"{}\">{}</text>",
                y + vega_fit::baseline_shift("top", t.size),
                vega_escape_attr(t.family),
                t.size,
                xml_escape_attr(&t.weight),
                xml_escape_attr(t.color),
                xml_escape(&t.text)
            );
        }
        let y = y + self.title_band();
        self.emit_def(svg, false);
        let _ = write!(
            svg,
            "<rect x=\"{x:.2}\" y=\"{y:.2}\" width=\"{length}\" height=\"{VEGA_GRADIENT_THICKNESS}\" \
             fill=\"url(#{})\"/>",
            xml_escape_attr(self.grad_id)
        );
        let label_y = y
            + VEGA_GRADIENT_THICKNESS
            + self.label_offset
            + vega_fit::baseline_shift("top", self.font_size);
        crate::contract::legend_label_open(svg, "");
        for (v, text) in self.values.iter().zip(self.labels) {
            let frac = unit_position(*v, self.color_min, self.color_max);
            let anchor = match frac {
                f if f <= 0.0 => "start",
                f if f >= 1.0 => "end",
                _ => "middle",
            };
            let _ = write!(
                svg,
                "<text x=\"{:.2}\" y=\"{label_y:.2}\" text-anchor=\"{anchor}\" font-family=\"{}\" \
                 font-size=\"{}\" font-weight=\"{}\" fill=\"{}\">{}</text>",
                x + length * frac,
                vega_escape_attr(self.family),
                self.font_size,
                xml_escape_attr(&self.weight),
                xml_escape_attr(self.color),
                xml_escape(text)
            );
        }
        crate::contract::mark_group_close(svg);
    }
}

/// What the facet's color channel paints.
enum FacetColor {
    Gradient { min: f64, max: f64, ticks: Vec<f64> },
    Categories(CategoryScale),
}

pub(crate) fn render_facet(
    payload: &Payload,
    fonts: &FontBook,
    r: &HeatmapResolved,
    categorical: bool,
) -> Result<String, Diagnostic> {
    let mc = r.multiples.as_ref().expect("render dispatched on multiples");
    let independent = mc.scale == "independent";
    let legend_edge = facet_compose::facet_legend_edge(&r.legend.position);
    let ax_x = &r.style.axis_x;
    let ax_y = &r.style.axis_y;
    let y_case = ax_y.labels.font.case.as_deref().unwrap_or("none");
    if ax_y.labels.align.as_deref() != Some("left") || matches!(y_case, "upper" | "lower") {
        return Err("heatmap: multiples needs own-side (left) y labels with no font case".into());
    }
    if r.panel_axes.is_empty() {
        return Err("heatmap: multiples with no baked panel_axes — resolve() should always supply at least one".into());
    }
    let axes = &r.panel_axes;
    let (rows, x_grain) = date_dimension(&payload.rows, &r.x, &r.style.axis_x, true)?;
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
    let x_narrowed = mc.rows.is_none() && panel_domain_is_proper_subset(&panels, axes, &r.x);
    let naive_panel_w = multiples::facet_panel_width(payload.width, panel_cols_n, false, 0.0);
    let y_label_family = ax_y.labels.font.family_or("sans-serif");
    let y_label_fs = ax_y.labels.font.size_or(11.0);
    let y_label_limit = ax_y.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    let y_raw_widths: Vec<(String, f64)> = y_shared
        .iter()
        .map(|band| fonts.measure(band, y_label_family, y_label_fs).map(|w| (band.clone(), w)))
        .collect::<Result<_, _>>()?;
    let widest_y = y_raw_widths.iter().fold(0.0_f64, |acc, (_, w)| acc.max(*w));
    let y_axis_px = if mc.columns.is_some() { facet_extra_axis_px(&ax_y.labels, &y_shared, fonts)? } else { 0.0 };
    let mirror = r.style.axis_y.mirror_requested("heatmap")?;
    // `facet_independent_channels`: a mirrored y never narrows, since the ghost
    // layer would be instantiated once per panel.
    let y_subset_narrowed = !mirror
        && panel_domain_is_proper_subset(&panels, axes, &r.y)
        && (mc.columns.is_none() || naive_panel_w - y_axis_px >= FACET_MIN_PANEL_PX);
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
    let x_axis = HeatmapXAxis::resolve(&x_domain, x_grain, ax_x, crowd_w, usable_ratio, fonts)?.with_title(x_title.clone(), &ax_x.title);
    let y_tick_len = ax_y.tick_len();
    let y_label_pad = widest_y.min(y_label_limit) + y_tick_len + BREATHING_ROOM_PX;
    let y_title = crate::common::axis_title_text(&r.y, r.y_label.as_deref(), &r.field_labels)?;
    crate::common::refuse_wrapping_facet_title("heatmap", &y_title, &ax_y.title, payload.height, panel_rows_n, fonts)?;
    let y_title_extra = crate::common::y_title_extra(&ax_y.title, &y_title);
    let y_label_ws: Vec<(String, f64)> = y_raw_widths.iter().map(|(b, w)| (b.clone(), w.min(y_label_limit))).collect();
    let color = if categorical {
        FacetColor::Categories(color_categories(r, &rows)?)
    } else {
        let (min, max, ticks) = gradient_ladder(&rows, &r.color, &r.style.color_gradient)?;
        FacetColor::Gradient { min, max, ticks }
    };
    let grad_id = format!("heat_grad-{}", crate::contract::safe_svg_id(&r.id));
    let cells = CellPaint {
        x_field: &r.x,
        y_field: &r.y,
        color_field: &r.color,
        fill: match &color {
            FacetColor::Gradient { min, max, .. } => {
                CellFill::Gradient { stops: &r.style.color_gradient.resolved_stops, min: *min, max: *max }
            }
            FacetColor::Categories(scale) => CellFill::Categories(scale),
        },
        opacity: r.style.rect_mark.opacity.unwrap_or(1.0),
        stroke_attr: r.style.rect_mark.stroke_attr()?,
        tooltip: tooltip_value_title(r, &rows)?.zip(r.style.tooltip_format.as_ref()),
    };
    let style = PanelStyle {
        ax_x,
        cell_x_axis,
        cells,
        y_font_size: y_label_fs,
        y_family: y_label_family,
        y_color: ax_y.labels.font.color.as_deref().unwrap_or("#626366"),
        y_weight: ax_y.labels.font.weight_str(),
        y_label_ws: &y_label_ws,
        y_label_pad,
        y_tick_len,
        own_axis: y_narrowed,
        y_right: ax_y.position.as_deref() == Some("right"),
        y_ticks: &ax_y.ticks,
        y_title: &y_title,
        y_title_style: &ax_y.title,
        y_title_extra,
        mirror,
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
        FacetColor::Gradient { ticks, .. } => ticks
            .iter()
            .map(|v| format_legend_value(*v, r.style.tooltip_format.as_ref().map(|f| f.spec.as_str())))
            .collect::<Result<_, _>>()?,
        FacetColor::Categories(_) => Vec::new(),
    };
    // The mirror's ghost rule layer strokes the same color, which turns the
    // gradient into a symbol legend: a circle per ladder rung.
    let ladder_symbols = style.mirror && matches!(color, FacetColor::Gradient { .. });
    let legend = match &color {
        FacetColor::Gradient { min, max, ticks } if !ladder_symbols => r
            .legend
            .visible
            .then(|| {
                GradientLegend::resolve(r, &grad_id, (*min, *max, ticks), &leg_labels, fonts)?
                    .with_title(&r.legend, legend_title.clone(), fonts)
            })
            .transpose()?,
        _ => None,
    };
    let ladder: Vec<(String, String)> = match &color {
        FacetColor::Gradient { min, max, ticks } if ladder_symbols => leg_labels
            .iter()
            .zip(ticks)
            .map(|(label, &tick)| Ok((label.clone(), interp_color(&r.style.color_gradient.resolved_stops, *min, *max, tick)?)))
            .collect::<Result<_, String>>()?,
        _ => Vec::new(),
    };
    let symbol_entries: Vec<LegendEntry> = match &color {
        FacetColor::Categories(scale) => category_legend_entries(scale, r.legend.values.as_deref()),
        FacetColor::Gradient { .. } => ladder
            .iter()
            .map(|(label, fill)| LegendEntry { label, fill, symbol: None, series_key: None })
            .collect(),
    };
    let entry_symbol = if ladder_symbols { LegendSymbol::Circle } else { LegendSymbol::FilledSquare };
    let symbol_legend = (r.legend.visible && (categorical || ladder_symbols))
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
                paint: Box::new(|svg, x, y, panel_w, panel_h| legend.paint(svg, x, y, legend.bar_len(panel_w, panel_h))),
            })
            .into_iter()
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
        mirror_in_panel: true,
        own_value_axes: independent,
        extra_axis_px,
        cell_x_axis,
        axis_x: ax_x,
        axis_y: &r.style.axis_y,
        header_font_family: &header_font_family,
        background: r.background.as_str(),
    };
    let row_axis = (!y_narrowed && ax_y.position.as_deref() != Some("right")).then_some(RowAxisSpec {
        reserve: y_label_pad + y_title_extra,
        label_x: AXIS_TRANSLATE - y_label_pad,
        title: &y_title,
        title_style: &ax_y.title,
    });
    facet_compose::compose_facet_full(payload, fonts, &spec, Some(&chrome), row_axis.as_ref(), &render_pass)
}

#[cfg(test)]
mod tests {
    use super::{facet_extra_axis_px, narrowed_domain, AxisLabels, ChartSort, CHROME_PX};
    use crate::text::FontBook;
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
}
