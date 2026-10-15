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

use crate::constants::layout::FACET_MIN_PANEL_PX;
use std::collections::HashSet;
use std::fmt::Write as _;

use serde_json::{Map, Value};

use crate::common::{row_str, AxisLabels, AxisStyle};
use crate::families::bar::CHROME_PX;
use crate::families::cartesian::BandScale;
use crate::families::facet_compose::{
    self, FacetChrome, FacetSpec, GhostStyle, LegendChrome, PanelGeometry, PanelXAxis, RowAxisSpec, TitleChrome,
};
use crate::families::facet_title::{layout_title, TitleLayout, TitleSource};
use crate::families::heatmap::{
    emit_gradient_def, format_legend_value, gradient_ladder, paint_cells, tooltip_value_title, CellFill, CellPaint,
    HeatmapResolved, AXIS_TRANSLATE, BREATHING_ROOM_PX, VEGA_GRADIENT_THICKNESS,
};
use crate::families::multiples::{self, Panel};
use crate::families::temporal::{
    detect_time_unit, parse_iso_date, refuse_authored_time_format, resolve_temporal_labels,
    x_values_are_temporal_candidates, TemporalLabel,
};
use crate::svg::{vega_escape_attr, xml_escape, xml_escape_attr};
use crate::text::FontBook;
use crate::vega_fit;
use crate::Payload;

/// A horizontal `orient: top` gradient legend is `clamp(unit width, 100, 200)`
/// long: Vega-Lite `defaultGradientLength` (`src/compile/legend/properties.ts`,
/// v6.4 lines 206-243) reads `model.getSizeSignalRef('width')`
/// (`src/compile/model.ts:493`), which for a unit inside a facet is the
/// unit's own `child_width`, and the bounds are `legendConfig.gradientHorizontal
/// {Min,Max}Length` (`src/legend.ts:194-195`).
const GRADIENT_LENGTH_MIN: f64 = 100.0;
const GRADIENT_LENGTH_MAX: f64 = 200.0;

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
    offset: f64,
    label_pad: f64,
    font_size: f64,
    family: String,
}

/// The x-axis settings neither heatmap arm draws: an authored angle or
/// `labelExpr`, hidden labels, or a calendar cadence. `lead` names the shape
/// that owns the refusal.
pub(crate) fn refuse_unmodeled_x_labels(ax_x: &AxisStyle, lead: &str) -> Result<(), String> {
    let labels_cfg = &ax_x.labels;
    if labels_cfg.angle.is_some() {
        return Err(format!("heatmap: {lead} an authored axis_x.labels.angle is not supported"));
    }
    if labels_cfg.expr.is_some() {
        return Err(format!("heatmap: {lead} an authored axis_x.labels.expr is not supported"));
    }
    if labels_cfg.visible == Some(false) {
        return Err(format!("heatmap: {lead} hidden axis_x labels is not supported"));
    }
    if ax_x.time_unit.as_ref().is_some_and(|v| !v.is_null()) {
        return Err(format!("heatmap: {lead} an authored axis_x.time_unit is not supported"));
    }
    if labels_cfg.time_unit.as_deref().is_some_and(|u| u != "auto") {
        return Err(format!("heatmap: {lead} an authored axis_x.labels.time_unit is not supported"));
    }
    Ok(())
}

impl HeatmapXAxis {
    /// `naive_panel_w` is the emitter's crowding width (`RenderBox.width`,
    /// `facet_panel_width`); `usable_ratio` the theme's `label_usable_ratio`.
    fn resolve(
        domain: &[String],
        ax_x: &AxisStyle,
        naive_panel_w: f64,
        usable_ratio: f64,
        fonts: &FontBook,
    ) -> Result<Self, String> {
        const LEAD: &str = "multiples with";
        refuse_unmodeled_x_labels(ax_x, LEAD)?;
        let refs: Vec<&str> = domain.iter().map(String::as_str).collect();
        if x_values_are_temporal_candidates(&refs) {
            return Self::dates(domain, ax_x, naive_panel_w, usable_ratio, fonts, LEAD);
        }
        // `resolve_axis_x_overlap`'s ordinal branch: a category axis whose
        // labels do not fit flat tilts, which this composite does not draw.
        let labels_cfg = &ax_x.labels;
        let angle =
            vega_fit::category_label_angle(labels_cfg, domain, naive_panel_w, Some(usable_ratio), fonts, labels_cfg.font.size_or(11.0))?;
        if vega_fit::normalize_angle(angle) != 0.0 {
            return Err("heatmap: multiples with tilted category x labels is not supported".into());
        }
        let measure = Self::measurer(ax_x, fonts);
        let labels = domain
            .iter()
            .enumerate()
            .map(|(domain_idx, d)| AxisLabel::measured(domain_idx, d.clone(), String::new(), &measure))
            .collect::<Result<_, _>>()?;
        Self::new(domain, ax_x, labels)
    }

    /// The calendar ladder over a date x: `resolve_axis_x_overlap`'s temporal
    /// branch, which keeps only the openers of the grain the crowding width
    /// affords (`resolve_temporal_labels`). `crowd_w` is the chart's box
    /// width (`RenderBox.width`), the same input the ordinal ladder reads.
    pub(crate) fn dates(
        domain: &[String],
        ax_x: &AxisStyle,
        crowd_w: f64,
        usable_ratio: f64,
        fonts: &FontBook,
        lead: &str,
    ) -> Result<Self, String> {
        let refs: Vec<&str> = domain.iter().map(String::as_str).collect();
        if refs.iter().any(|d| parse_iso_date(d).is_none()) {
            return Err(format!("heatmap: {lead} a date-like x that is not ISO dates is not supported"));
        }
        if !refs.windows(2).all(|w| w[0] < w[1]) {
            return Err(format!("heatmap: {lead} a date x not in ascending query order is not supported"));
        }
        refuse_authored_time_format("heatmap", ax_x.labels.spec())?;
        let data_tu = detect_time_unit(&refs)
            .ok_or_else(|| format!("heatmap: {lead} a date x at no detectable calendar grain is not supported"))?;
        crate::families::temporal::refuse_grain_format("heatmap", &ax_x.grain_formats, data_tu)?;
        let font_size = ax_x.labels.font.size_or(11.0);
        let mut measure = |s: &str| ax_x.labels.font.measure_weighted(fonts, s, font_size);
        let (temporal, _visibility) = resolve_temporal_labels(
            domain,
            data_tu,
            None,
            crowd_w * usable_ratio,
            false,
            true,
            false,
            ax_x.fiscal_year_start_month,
            ax_x.labels.overlap.as_ref().is_some_and(|o| o.skip),
            None,
            &mut measure,
        )?;
        let measure = Self::measurer(ax_x, fonts);
        let labels = temporal
            .into_iter()
            .map(|TemporalLabel { domain_idx, row1, row2 }| AxisLabel::measured(domain_idx, row1, row2, &measure))
            .collect::<Result<_, _>>()?;
        Self::new(domain, ax_x, labels)
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
            offset: ax_x.ticks.offset.unwrap_or(0.0),
            label_pad: ax_x.labels.padding.ok_or("heatmap: axis_x.labels.padding absent (theme required)")?,
            font_size: ax_x.labels.font.size_or(11.0),
            family: ax_x.labels.font.family_or("sans-serif").to_string(),
        })
    }

    /// How far the label rows reach below the axis's label anchor.
    pub(crate) fn label_reach(&self) -> f64 {
        if self.labels.iter().any(|l| !l.row2.is_empty()) {
            self.font_size + vega_fit::label_row_advance(self.font_size)
        } else {
            self.font_size
        }
    }

    /// Joins every drawn label row to the fit's bounds, below a plot
    /// `plot_w` x `plot_h`. A second row sits `fontSize + 2` under the first.
    pub(crate) fn union_ink(&self, b: &mut vega_fit::Bounds, plot_w: f64, plot_h: f64) {
        let scale = BandScale::new(self.domain.clone(), 0.0, plot_w, 0.0, 0.0);
        let row_y = plot_h + self.offset + self.tick_len + self.label_pad;
        for label in &self.labels {
            let center = scale.position(&self.domain[label.domain_idx]).expect("a label names a band of its own domain")
                + scale.bandwidth() / 2.0;
            b.union(&vega_fit::text_ink(center, row_y, label.w1, self.font_size, "center", "top"));
            if !label.row2.is_empty() {
                let row2_y = row_y + vega_fit::label_row_advance(self.font_size);
                b.union(&vega_fit::text_ink(center, row2_y, label.w2, self.font_size, "center", "top"));
            }
        }
    }

    /// Paints at the CALLER's `plot_left`/`plot_w`/`plot_bottom`. The axis
    /// group sits `offset` plus Vega's half-pixel crisp translate below the
    /// plot; a tick snaps to a whole pixel (`floor(center) + 0.5`) while its
    /// label keeps the exact band center.
    pub(crate) fn paint(&self, svg: &mut String, ax_x: &AxisStyle, plot_left: f64, plot_w: f64, plot_bottom: f64) {
        let scale = BandScale::new(self.domain.clone(), 0.0, plot_w, 0.0, 0.0);
        let axis_top = plot_bottom + self.offset + AXIS_TRANSLATE;
        let label_y = axis_top + self.tick_len + self.label_pad + vega_fit::baseline_shift("top", self.font_size);
        let row2_y = label_y + vega_fit::label_row_advance(self.font_size);
        let tick_color = ax_x.ticks.color.as_deref().unwrap_or("#D0D3D9");
        let tick_width = ax_x.ticks.width.unwrap_or(1.0);
        let label_color = ax_x.labels.font.color.as_deref().unwrap_or("#626366");
        let label_weight = ax_x.labels.font.weight_str();
        crate::contract::axis_group_open(svg, crate::contract::AxisChannel::X, (plot_left + AXIS_TRANSLATE, axis_top));
        for label in &self.labels {
            let band_x = scale
                .position(&self.domain[label.domain_idx])
                .expect("a label names a band of its own domain");
            let center = band_x + scale.bandwidth() / 2.0;
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
            for (text, y) in [(&label.row1, label_y), (&label.row2, row2_y)] {
                if text.is_empty() {
                    continue;
                }
                let _ = write!(
                    svg,
                    "<text x=\"{:.2}\" y=\"{y:.2}\" text-anchor=\"middle\" font-family=\"{}\" \
                     font-size=\"{}\" font-weight=\"{}\" fill=\"{}\">{}</text>",
                    plot_left + center,
                    vega_escape_attr(&self.family),
                    self.font_size,
                    xml_escape_attr(&label_weight),
                    xml_escape_attr(label_color),
                    xml_escape(text)
                );
            }
        }
        crate::contract::axis_group_close(svg);
    }
}

/// The width one extra per-column-panel y axis would cost
/// (`facet_extra_axis_width_px`, `_cartesian.py:1081-1156`): the chrome plus the
/// widest band label, clamped to the label limit. The labels are measured at the
/// weight they paint at (`:1151`), so a bold axis costs more than a regular one.
fn facet_extra_axis_px(labels: &AxisLabels, bands: &[String], fonts: &FontBook) -> Result<f64, String> {
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
fn panel_domain_is_proper_subset(panels: &[Panel], axes: &[multiples::PartitionAxis], field: &str) -> bool {
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

/// The gradient legend's length for a unit `plot_w` wide.
pub(crate) fn gradient_length(plot_w: f64) -> f64 {
    plot_w.clamp(GRADIENT_LENGTH_MIN, GRADIENT_LENGTH_MAX)
}

/// A horizontal gradient legend's length on `edge`: the same
/// `defaultGradientLength` gives a top or bottom one the clamped unit width
/// and a side one `gradientHorizontalMinLength` alone.
pub(crate) fn horizontal_gradient_length(edge: &str, plot_w: f64) -> f64 {
    if matches!(edge, "top" | "bottom") {
        gradient_length(plot_w)
    } else {
        GRADIENT_LENGTH_MIN
    }
}

/// Everything a panel paint reads that is common to the whole composite.
struct PanelStyle<'a> {
    x_domain: &'a [String],
    x_axis: &'a HeatmapXAxis,
    cells: CellPaint<'a>,
    y_font_size: f64,
    y_family: &'a str,
    y_color: &'a str,
    y_weight: String,
    y_label_ws: &'a [(String, f64)],
    /// `labelPadding`: where a left-anchored label starts, left of the plot.
    y_label_pad: f64,
    /// A narrowed y draws its axis inside every cell; a shared one is the
    /// composer's row-header axis.
    own_axis: bool,
}

fn render_panel(
    style: &PanelStyle,
    rows: &[Map<String, Value>],
    y_domain: &[String],
    plot_w: f64,
    plot_h: f64,
) -> Result<(String, PanelGeometry), String> {
    let x_scale = BandScale::new(style.x_domain.to_vec(), 0.0, plot_w, 0.0, 0.0);
    let y_scale = BandScale::new(y_domain.to_vec(), 0.0, plot_h, 0.0, 0.0);
    let label_w = |band: &str| {
        style.y_label_ws.iter().find(|(label, _)| label == band).map(|(_, w)| *w).expect("label measured")
    };
    let label_x = AXIS_TRANSLATE - style.y_label_pad;
    let centers: Vec<(&String, f64)> = y_domain
        .iter()
        .map(|band| (band, y_scale.position(band).expect("band in its own domain") + y_scale.bandwidth() / 2.0))
        .collect();
    let label_ink = |cy: f64, w: f64| vega_fit::text_ink(label_x, cy, w, style.y_font_size, "left", "middle");
    let margins = vega_fit::pad_plot(plot_w, plot_h, |_, _, b| {
        if style.own_axis {
            for &(band, cy) in &centers {
                b.union(&label_ink(cy, label_w(band)));
            }
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
    if style.own_axis {
        crate::contract::axis_group_open(&mut svg, crate::contract::AxisChannel::Y, (left + label_x, 0.0));
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
        crate::contract::axis_group_close(&mut svg);
    }
    let svg_end = svg.len();
    svg.push_str("</svg>");

    let mut ink = vega_fit::Bounds::new(left, 0.0, left + plot_w, plot_h);
    if style.own_axis {
        for &(band, cy) in &centers {
            let b = label_ink(cy, label_w(band));
            ink.union(&vega_fit::Bounds::new(left + b.x1, b.y1, left + b.x2, b.y2));
        }
    }
    let geometry = PanelGeometry {
        content_start,
        svg_end,
        top_axis_overhang: (-ink.y1).max(0.0),
        legend: None,
        ink,
        axis_w_overhang: left,
        left_mark_overhang: left,
        raw_right_reach: 0.0,
        widest_val_tick_label_width: 0.0,
        val_gutter: 0.0,
        val_tick_size: 0.0,
        val_tick_marks: Vec::new(),
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
        axis: PanelXAxis::Heat(style.x_axis.clone()),
    };
    Ok((svg, geometry))
}

/// The horizontal gradient legend: a `length x 16` bar, its ladder labels a
/// `labelOffset` below, the end labels flush to the bar's ends.
pub(crate) struct GradientLegend<'a> {
    grad_id: &'a str,
    stops: &'a [String],
    values: &'a [f64],
    labels: &'a [String],
    color_min: f64,
    color_max: f64,
    label_offset: f64,
    font_size: f64,
    family: &'a str,
    color: &'a str,
    weight: String,
}

impl<'a> GradientLegend<'a> {
    /// The legend over `r`'s resolved gradient and label style, its ladder
    /// already formatted into `labels`.
    pub(crate) fn resolve(
        r: &'a HeatmapResolved,
        grad_id: &'a str,
        (color_min, color_max, values): (f64, f64, &'a [f64]),
        labels: &'a [String],
        lead: &str,
    ) -> Result<Self, String> {
        if color_max <= color_min {
            return Err(format!("heatmap: {lead} a degenerate color domain is not supported"));
        }
        let font = &r.legend.label.font;
        Ok(GradientLegend {
            grad_id,
            stops: &r.style.color_gradient.resolved_stops,
            values,
            labels,
            color_min,
            color_max,
            label_offset: r
                .legend
                .label
                .padding
                .ok_or("heatmap: legend.label.padding absent (theme required)")?,
            font_size: font.size_or(11.0),
            family: font.family_or("sans-serif"),
            color: font.color.as_deref().unwrap_or("#626366"),
            weight: font.weight_str(),
        })
    }

    pub(crate) fn height(&self) -> f64 {
        VEGA_GRADIENT_THICKNESS + self.label_offset + self.font_size
    }

    /// Vega does not thin a gradient legend's labels, so labels that touch
    /// would paint over each other where production shows them as drawn.
    pub(crate) fn check_labels_clear(&self, fonts: &FontBook, length: f64, lead: &str) -> Result<(), String> {
        let last = self.labels.len() - 1;
        let mut previous_right = f64::NEG_INFINITY;
        for (i, (v, text)) in self.values.iter().zip(self.labels).enumerate() {
            let w = fonts.measure_shaped(text, self.family, self.font_size)?;
            let at = length * (v - self.color_min) / (self.color_max - self.color_min);
            let left = match i {
                0 => at,
                _ if i == last => at - w,
                _ => at - w / 2.0,
            };
            if left < previous_right {
                return Err(format!("heatmap: {lead} overlapping gradient legend labels is not supported"));
            }
            previous_right = left + w;
        }
        Ok(())
    }

    pub(crate) fn paint(&self, svg: &mut String, x: f64, y: f64, length: f64) {
        emit_gradient_def(svg, self.grad_id, self.stops, false);
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
        let span = self.color_max - self.color_min;
        let last = self.labels.len() - 1;
        crate::contract::legend_label_open(svg, "");
        for (i, (v, text)) in self.values.iter().zip(self.labels).enumerate() {
            let anchor = match i {
                0 => "start",
                _ if i == last => "end",
                _ => "middle",
            };
            let _ = write!(
                svg,
                "<text x=\"{:.2}\" y=\"{label_y:.2}\" text-anchor=\"{anchor}\" font-family=\"{}\" \
                 font-size=\"{}\" font-weight=\"{}\" fill=\"{}\">{}</text>",
                x + length * (v - self.color_min) / span,
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

pub(crate) fn render_facet(payload: &Payload, fonts: &FontBook, r: &HeatmapResolved) -> Result<String, String> {
    let mc = r.multiples.as_ref().expect("render dispatched on multiples");
    for (name, axis) in [("axis_x", &r.style.axis_x), ("axis_y", &r.style.axis_y)] {
        if axis.title.visible != Some(false) {
            return Err(format!("heatmap: multiples with a visible {name} title is not supported"));
        }
    }
    if mc.scale == "independent" {
        return Err("heatmap: multiples.scale independent not supported yet — only shared".into());
    }
    if r.sort.is_some() {
        return Err("heatmap: multiples with an authored sort is not supported".into());
    }
    if r.legend.visible {
        if r.legend.position.edge != "top" || r.legend.direction != "horizontal" {
            return Err(format!(
                "heatmap: multiples with a {} {} legend not supported — only a horizontal top legend",
                r.legend.direction, r.legend.position.edge
            ));
        }
        if r.legend.title.visible != Some(false) {
            return Err("heatmap: multiples with a legend title not supported — the resolver hides it".into());
        }
    }
    let ax_x = &r.style.axis_x;
    let ax_y = &r.style.axis_y;
    let y_case = ax_y.labels.font.case.as_deref().unwrap_or("none");
    if ax_y.labels.align.as_deref() != Some("left") || matches!(y_case, "upper" | "lower") {
        return Err("heatmap: multiples needs own-side (left) y labels with no font case".into());
    }
    if ax_y.ticks.visible.unwrap_or(true) {
        return Err("heatmap: multiples with visible y axis ticks is not supported".into());
    }
    if r.style.rect_mark.stroke_width.unwrap_or(0.0) > 0.0 && r.style.rect_mark.stroke.as_deref().unwrap_or("none") != "none" {
        return Err("heatmap: multiples with a rect mark stroke is not supported".into());
    }
    if r.panel_axes.is_empty() {
        return Err("heatmap: multiples with no baked panel_axes — resolve() should always supply at least one".into());
    }
    let axes = &r.panel_axes;
    let panels = multiples::regroup(axes, &payload.rows).map_err(|e| format!("heatmap: {e}"))?;
    let panel_rows_n = multiples::panel_axis_cardinality(axes, mc.rows.as_deref());
    let panel_cols_n = multiples::panel_axis_cardinality(axes, mc.columns.as_deref());
    if panels.len() != panel_rows_n * panel_cols_n {
        return Err("heatmap: multiples produced a sparse facet grid — not supported yet, only a fully populated rows x columns grid".into());
    }
    let index_of = |field: &Option<String>| field.as_deref().and_then(|f| axes.iter().position(|a| a.field == f));

    // ── Which scales FacetFeature frees per panel ─────────────────────────
    let x_domain = first_appearance(&payload.rows, &r.x);
    let y_shared = first_appearance(&payload.rows, &r.y);
    if x_domain.is_empty() || y_shared.is_empty() {
        return Err("heatmap: multiples with no x or y values".into());
    }
    if mc.rows.is_none() && panel_domain_is_proper_subset(&panels, axes, &r.x) {
        return Err("heatmap: multiples whose panels hold different x values is not supported — the x axis would be drawn per panel".into());
    }
    let naive_panel_w = multiples::facet_panel_width(payload.width, panel_cols_n, false, 0.0);
    let y_label_family = ax_y.labels.font.family_or("sans-serif");
    let y_label_fs = ax_y.labels.font.size_or(11.0);
    let y_label_limit = ax_y.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    let y_raw_widths: Vec<(String, f64)> = y_shared
        .iter()
        .map(|band| fonts.measure(band, y_label_family, y_label_fs).map(|w| (band.clone(), w)))
        .collect::<Result<_, _>>()?;
    let widest_y = y_raw_widths.iter().fold(0.0_f64, |acc, (_, w)| acc.max(*w));
    let y_narrowed = panel_domain_is_proper_subset(&panels, axes, &r.y) && {
        if mc.columns.is_none() {
            true
        } else {
            let extra = facet_extra_axis_px(&ax_y.labels, &y_shared, fonts)?;
            if naive_panel_w - extra >= FACET_MIN_PANEL_PX {
                return Err("heatmap: multiples narrowing the y axis per column panel is not supported".into());
            }
            false
        }
    };

    // ── Axes and color ───────────────────────────────────────────────────
    let usable_ratio = r
        .style
        .label_usable_ratio
        .ok_or("heatmap: label_usable_ratio absent (theme required for the x axis ladder)")?;
    let x_axis = HeatmapXAxis::resolve(&x_domain, ax_x, naive_panel_w, usable_ratio, fonts)?;
    let y_label_pad = widest_y.min(y_label_limit) + BREATHING_ROOM_PX;
    let y_label_ws: Vec<(String, f64)> = y_raw_widths.iter().map(|(b, w)| (b.clone(), w.min(y_label_limit))).collect();
    let (color_min, color_max, leg_values) = gradient_ladder(&payload.rows, &r.color, &r.style.color_gradient)?;
    let grad_id = format!("heat_grad-{}", crate::contract::safe_svg_id(&r.id));
    let cells = CellPaint {
        x_field: &r.x,
        y_field: &r.y,
        color_field: &r.color,
        fill: CellFill::Gradient { stops: &r.style.color_gradient.resolved_stops, min: color_min, max: color_max },
        opacity: r.style.rect_mark.opacity.unwrap_or(1.0),
        stroke_attr: String::new(),
        tooltip: tooltip_value_title(r, &payload.rows)?.zip(r.style.tooltip_format.as_ref()),
    };
    let style = PanelStyle {
        x_domain: &x_domain,
        x_axis: &x_axis,
        cells,
        y_font_size: y_label_fs,
        y_family: y_label_family,
        y_color: ax_y.labels.font.color.as_deref().unwrap_or("#626366"),
        y_weight: ax_y.labels.font.weight_str(),
        y_label_ws: &y_label_ws,
        y_label_pad,
        own_axis: y_narrowed,
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
        .map(|rows| if y_narrowed { first_appearance(rows, &r.y) } else { y_shared.clone() })
        .collect();
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
    let title_plan = match layout_title(&title_src, fonts, avail_w, true)? {
        TitleLayout::Plan(plan) => Some(plan),
        TitleLayout::Untitled => None,
        TitleLayout::Overflow => unreachable!("layout_title wraps an over-wide title when asked to"),
    };
    let leg_labels: Vec<String> = leg_values
        .iter()
        .map(|v| format_legend_value(*v, r.style.tooltip_format.as_ref().map(|f| f.spec.as_str())))
        .collect::<Result<_, _>>()?;
    const LEAD: &str = "multiples with";
    let legend = r
        .legend
        .visible
        .then(|| GradientLegend::resolve(r, &grad_id, (color_min, color_max, &leg_values), &leg_labels, LEAD))
        .transpose()?;
    let render_pass = |plot_w: f64, plot_h: f64, _value_axis_right_overhang: f64| {
        if let Some(legend) = &legend {
            legend.check_labels_clear(fonts, gradient_length(plot_w), LEAD)?;
        }
        panel_rows
            .iter()
            .zip(&panel_y_domains)
            .map(|(rows, y_domain)| render_panel(&style, rows, y_domain, plot_w, plot_h))
            .collect::<Result<Vec<_>, String>>()
    };
    let chrome = FacetChrome {
        title: title_plan.as_ref().map(|plan| TitleChrome {
            ink: plan.ink.clone(),
            truncated: false,
            paint: Box::new(|svg, x, y| plan.paint(&title_src, svg, x, y)),
        }),
        legend: legend.as_ref().map(|legend| LegendChrome {
            width: Box::new(gradient_length),
            height: legend.height(),
            paint: Box::new(|svg, x, y, panel_w| legend.paint(svg, x, y, gradient_length(panel_w))),
        }),
    };
    let spec = FacetSpec {
        family: "heatmap",
        panels: &panels,
        panel_rows_n,
        panel_cols_n,
        row_axis_idx: index_of(&mc.rows),
        col_axis_idx: index_of(&mc.columns),
        has_mirror: false,
        axis_x: ax_x,
        axis_y: &r.style.axis_y,
        header_font_family: &header_font_family,
        background: r.background.as_str(),
    };
    let row_axis = (!y_narrowed).then_some(RowAxisSpec { reserve: y_label_pad, label_x: AXIS_TRANSLATE - y_label_pad });
    facet_compose::compose_facet_full(payload, fonts, &spec, Some(&chrome), row_axis.as_ref(), &render_pass)
}

#[cfg(test)]
mod tests {
    use super::{facet_extra_axis_px, gradient_length, AxisLabels, CHROME_PX};
    use crate::text::FontBook;

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

    /// `clamp(unit width, 100, 200)`: the floor holds below 100, the unit width
    /// passes through between the bounds, the ceiling holds above 200.
    #[test]
    fn a_horizontal_gradient_is_the_unit_width_clamped_to_100_and_200() {
        let probed = [(50.0, 100.0), (64.0, 100.0), (100.0, 100.0), (150.0, 150.0), (199.0, 199.0), (200.0, 200.0), (240.0, 200.0), (300.0, 200.0)];
        for (unit, length) in probed {
            assert_eq!(gradient_length(unit), length, "unit width {unit}");
        }
    }
}
