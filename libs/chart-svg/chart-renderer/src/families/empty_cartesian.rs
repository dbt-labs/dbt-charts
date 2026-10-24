//! A Vega-Lite cartesian chart whose query returned no rows (bar, line, area,
//! scatter, heatmap): what Vega leaves to paint is the title, the axes' domain
//! lines, titles and rungs, and the legends. The laws are in `docs/laws-empty.md`.

use std::cell::Cell;
use std::fmt::Write as _;

use serde::Deserialize;

use crate::common::{AxisStyle, AxisTitle, FieldLabels};
use crate::contract::AxisChannel;
use crate::families::axis_caption::{vega_axis_format, AxisCaption, CaptionScale, NumericScale};
use crate::diagnostic::Diagnostic;
use crate::families::bar::{flush_edge, flush_threshold, format_tick_labels};
use crate::families::cartesian::{vl_default_tick_count, LinearScale};
use crate::families::empty::{Head, LegendKind};
use crate::families::layers::{LayerSide, OverlayLayer, YSides};
use crate::families::legend::{
    emit_vega_legend, legend_fit_bounds, legend_origin, slide_block, stack_legends, vega_legend,
    LegendConfig, LegendEntry, LegendSymbol, VegaLegend, VEGA_LEGEND_OFFSET,
};
use crate::families::scatter_shape::{ShapeLegend, SymbolFill};
use crate::frame::ChartFrame;
use crate::svg::{num_full};
use print::{vega_escape_attr, xml_escape, xml_escape_attr};
use measure::FontBook;
use crate::vega_fit;
use crate::common::VALUE_AXIS_BREATHING_ROOM;
use crate::Payload;

/// The scale on a zero-row chart's value axis.
pub(crate) enum ValueAxis {
    /// A linear measure scale. `horizontal` puts it along the bottom and the
    /// category axis beside the plot; `zero_rung` draws the single rung of an
    /// empty zero-based scale.
    Linear { horizontal: bool, zero_rung: bool },
    /// A point or band scale over no values: no rung, unless `pins_domain`
    /// reads an authored domain pair as the scale's two category values.
    Point { pins_domain: bool },
}

#[derive(Deserialize)]
struct Extra {
    x: Option<String>,
    y: Option<String>,
    #[serde(default)]
    x_label: Option<String>,
    #[serde(default)]
    y_label: Option<String>,
    field_labels: FieldLabels,
    legend: LegendConfig,
}

/// Which panels a small-multiples chart over no rows has none of.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum Facet {
    /// No columns: a plot rect with no width, only the axes beside it.
    Columns,
    /// No rows: a plot rect with no height, only the axes under it.
    Rows,
    /// Neither: nothing is drawn.
    Both,
}

/// An axis title laid out: the text, its font size and padding, its width, and
/// the fit ink it reserves past the labels.
#[derive(Clone)]
struct Title {
    style: AxisTitle,
    text: String,
    size: f64,
    pad: f64,
    width: f64,
    extra: f64,
}

/// What every value axis of a chart shares: the label font and the label rules.
struct LabelStyle<'a> {
    family: &'a str,
    size: f64,
    pad: f64,
    limit: f64,
    separation: f64,
    flush: Option<f64>,
    angle: f64,
}

/// One value axis: its scale, rungs and the chrome around them.
#[derive(Clone)]
struct ValueAxisPlan {
    horizontal: bool,
    /// `1` for an axis right of the plot, `-1` for one left of it.
    side: f64,
    offset: f64,
    /// A point scale places rung `i` of `domain.1` at `(i + 0.5)` steps; a linear one at its value.
    point: bool,
    domain: (f64, f64),
    rungs: Vec<f64>,
    labels: Vec<String>,
    widths: Vec<f64>,
    tick_len: f64,
    /// The label padding past the ticks.
    pad: f64,
    labels_shown: bool,
    /// Labels growing away from the plot, left-anchored (a right axis with no measured gutter).
    away: bool,
    gutter: f64,
    title: Option<Title>,
    line: Option<(f64, String)>,
    /// Gridlines belong to the plot cell, which a facet with no panels lacks.
    grid: bool,
    /// A mirrored copy repeats the labels and the domain line, never the title.
    ghost: bool,
}

impl ValueAxisPlan {
    fn pos(&self, value: f64, pw: f64, ph: f64) -> f64 {
        match (self.point, self.horizontal) {
            (true, _) => LinearScale::new(self.domain.0, self.domain.1, 0.0, ph).position(value),
            (false, true) => LinearScale::new(self.domain.0, self.domain.1, 0.0, pw).position(value),
            (false, false) => LinearScale::new(self.domain.0, self.domain.1, ph, 0.0).position(value),
        }
    }

    /// `(labelAlign, labelBaseline)`: along the bottom `flush` pins the end
    /// labels to the range ends; beside a vertical axis the side and gutter decide.
    fn anchor(&self, st: &LabelStyle, pos: f64, pw: f64) -> (&'static str, &'static str) {
        if !self.horizontal {
            (if self.side > 0.0 && self.away { "left" } else if self.side > 0.0 { "right" } else { "right" }, "middle")
        } else if st.angle == 0.0 {
            match flush_edge(pos, pw, st.flush) {
                Some(true) => ("left", "top"),
                Some(false) => ("right", "top"),
                None => ("center", "top"),
            }
        } else {
            vega_fit::vl_x_label_anchor(st.angle, true)
        }
    }

    /// Where a vertical axis' labels anchor, from its line.
    fn label_dx(&self) -> f64 {
        self.side * self.tick_len + if self.side > 0.0 && !self.away { self.gutter } else { self.side * self.pad }
    }

    fn label_ink(&self, st: &LabelStyle, x: f64, y: f64, w: f64, align: &str, baseline: &str) -> vega_fit::Bounds {
        let text = vega_fit::text_ink(x, y, w.min(st.limit), st.size, align, baseline);
        if st.angle == 0.0 { text } else { text.rotate(st.angle, x, y) }
    }

    /// Every label's ink relative to the axis line, and the parity overlap's verdict.
    fn label_inks(&self, st: &LabelStyle, pw: f64, ph: f64) -> (Vec<vega_fit::Bounds>, Vec<bool>) {
        let inks: Vec<_> = self
            .rungs
            .iter()
            .zip(&self.widths)
            .map(|(&value, &w)| {
                let pos = self.pos(value, pw, ph);
                let (align, baseline) = self.anchor(st, pos, pw);
                if self.horizontal {
                    self.label_ink(st, pos, self.tick_len + self.pad, w, align, baseline)
                } else {
                    self.label_ink(st, self.label_dx(), pos, w, align, baseline)
                }
            })
            .collect();
        let visible = vega_fit::parity_visible(&inks, st.separation);
        (inks, visible)
    }

    /// How far the axis' ink reaches past its line along the label direction,
    /// which the title sits beyond: ticks, then the drawn labels.
    fn reach(&self, st: &LabelStyle) -> f64 {
        if !self.labels_shown {
            return self.tick_len;
        }
        let (inks, visible) = self.label_inks(st, 0.0, 0.0);
        inks.iter()
            .zip(&visible)
            .filter(|(_, &v)| v)
            .map(|(ink, _)| if self.horizontal { ink.y2 } else { self.side * if self.side > 0.0 { ink.x2 } else { ink.x1 } })
            .fold(self.tick_len, f64::max)
    }

    /// The axis group's ink in plot coordinates.
    fn ink(&self, st: &LabelStyle, pw: f64, ph: f64) -> vega_fit::Bounds {
        let reach = self.reach(st);
        let (inks, visible) = self.label_inks(st, pw, ph);
        let mut axis;
        if self.horizontal {
            let y = ph + self.offset;
            axis = vega_fit::Bounds::new(0.0, y, 0.0, y);
            for ink in inks.iter().zip(&visible).filter(|(_, &v)| v && self.labels_shown).map(|(ink, _)| ink) {
                axis.union(&vega_fit::Bounds::new(ink.x1, ink.y1 + y, ink.x2, ink.y2 + y));
            }
            if self.tick_len > 0.0 {
                for &value in &self.rungs {
                    let x = self.pos(value, pw, ph);
                    axis.union(&vega_fit::Bounds::new(x, y, x, y + self.tick_len));
                }
            }
            if let Some(t) = &self.title {
                axis.union(&vega_fit::text_ink(pw / 2.0, y + reach + t.pad, t.width, t.size, "center", "top"));
            }
        } else {
            let x = if self.side > 0.0 { pw + self.offset } else { -self.offset };
            axis = vega_fit::Bounds::new(x, 0.0, x, 0.0);
            let edge = x + self.side * self.tick_len;
            if self.tick_len > 0.0 {
                for &value in &self.rungs {
                    let y = self.pos(value, pw, ph);
                    axis.union(&vega_fit::Bounds::new(x.min(edge), y, x.max(edge), y));
                }
            }
            for (ink, _) in inks.iter().zip(&visible).filter(|(_, &v)| v && self.labels_shown) {
                axis.union(&vega_fit::Bounds::new(ink.x1 + x, ink.y1, ink.x2 + x, ink.y2));
            }
            if let Some(t) = &self.title {
                let title_edge = x + self.side * (reach + t.extra);
                axis.union(&vega_fit::Bounds::new(x.min(title_edge), ph / 2.0 - t.width / 2.0, x.max(title_edge), ph / 2.0 + t.width / 2.0));
            }
        }
        axis
    }
}

/// The category axis: its domain line and title, on the bottom of a vertical
/// chart or beside a horizontal bar.
struct CategoryAxis {
    horizontal: bool,
    side: f64,
    offset: f64,
    title: Option<Title>,
    line: Option<(f64, String)>,
}

impl CategoryAxis {
    fn ink(&self, pw: f64, ph: f64) -> vega_fit::Bounds {
        if self.horizontal {
            let x = if self.side > 0.0 { pw + self.offset } else { -self.offset };
            let mut axis = vega_fit::Bounds::new(x, 0.0, x, 0.0);
            if let Some(t) = &self.title {
                let edge = x + self.side * t.extra;
                axis.union(&vega_fit::Bounds::new(x.min(edge), ph / 2.0 - t.width / 2.0, x.max(edge), ph / 2.0 + t.width / 2.0));
            }
            axis
        } else {
            let y = ph + self.offset;
            let mut axis = vega_fit::Bounds::new(0.0, y, 0.0, y);
            if let Some(t) = &self.title {
                axis.union(&vega_fit::text_ink(pw / 2.0, y + t.pad, t.width, t.size, "center", "top"));
            }
            axis
        }
    }
}

/// A zero-row legend laid out: a themed Vega legend, or a shape channel's
/// default-styled one.
enum LegendBox {
    Vega(VegaLegend),
    Shape(ShapeLegend),
}

impl LegendBox {
    fn size(&self) -> (f64, f64) {
        match self {
            LegendBox::Vega(lg) => (lg.box_w, lg.box_h()),
            LegendBox::Shape(lg) => (lg.w, lg.h),
        }
    }

    fn paint(&self, svg: &mut String, entries: &[LegendEntry], cfg: &LegendConfig, x: f64, y: f64) {
        match self {
            LegendBox::Vega(lg) => emit_vega_legend(svg, entries, lg, cfg, LegendSymbol::Circle, x, y, false),
            LegendBox::Shape(lg) => lg.paint(svg, x, y, SymbolFill::Static("#000"), 1.0),
        }
    }
}

fn domain_line(ax: &AxisStyle, family: &str, axis: &str) -> Result<Option<(f64, String)>, String> {
    if !ax.line_visible() {
        return Ok(None);
    }
    let line = ax.line.as_ref().ok_or_else(|| format!("{family}: {axis}.line absent (theme required)"))?;
    let width = line.width.ok_or_else(|| format!("{family}: {axis}.line.width absent (theme required)"))?;
    let color = line.color.as_deref().ok_or_else(|| format!("{family}: {axis}.line.color absent (theme required)"))?;
    Ok(Some((width, xml_escape_attr(color))))
}

fn title_of(family: &str, axis: &str, style: &AxisTitle, text: String, fonts: &FontBook) -> Result<Option<Title>, String> {
    if crate::common::axis_title_band(style, &text) <= 0.0 {
        return Ok(None);
    }
    let (size, font, pad) = crate::families::empty::require_title_theme(family, axis, style)?;
    let width = fonts.measure(&text, font, size)?;
    let extra = crate::common::y_title_extra(style, &text);
    Ok(Some(Title { style: style.clone(), text, size, pad, width, extra }))
}

pub(crate) fn render(
    family: &str,
    payload: &Payload,
    fonts: &FontBook,
    value_axis: ValueAxis,
) -> Result<String, Diagnostic> {
    let (horizontal, zero_rung, pins_point_domain) = match value_axis {
        ValueAxis::Linear { horizontal, zero_rung } => (horizontal, zero_rung, None),
        ValueAxis::Point { pins_domain } => (false, false, Some(pins_domain)),
    };
    let head = Head::parse(family, &payload.resolved)?;
    let extra: Extra = Deserialize::deserialize(&payload.resolved).map_err(|e| format!("{family} payload: {e}"))?;
    let (ax_x, ax_y) = (&head.style.axis_x, &head.style.axis_y);
    let position_right = crate::families::empty::value_axis_on_right(family, ax_y)?;
    let facet = head.facet();
    // Once a layer pins a side, every layer owns an axis and the base takes the free side
    // (`_overlay.py`): a horizontal bar has none to give (`ERR-LAYER-AXIS-POSITION-ORIENTATION`,
    // raised ahead of this drawing).
    let layers = head.overlay_layers(family)?;
    let ysides = YSides::of(&layers).filter(|_| !horizontal && facet.is_none());
    let side_right = ysides.as_ref().map_or(position_right, |s| s.base == LayerSide::Right);
    let side = if side_right { 1.0 } else { -1.0 };
    // A base re-pinned to another edge loses its `labelAlign` and `labelPadding`, taking Vega's
    // default of 2; every axis of a dual-axis chart is titled so the scales tell apart, at Vega's
    // default padding of 4 (a scatter's axes keep the theme's titles).
    let moved = side_right != position_right;
    let dual_titles = ysides.is_some() && family != "scatter";
    let frame = ChartFrame::of(payload, &head.background);
    let title = head.title(&frame, fonts)?;

    let require = |what: &str, value: Option<f64>| value.ok_or_else(|| format!("{family}: axis_y.labels.{what} absent (theme required)"));
    let val_family = ax_y
        .labels
        .font
        .family
        .as_deref()
        .ok_or_else(|| format!("{family}: axis_y.labels.font.family absent (theme required)"))?;
    let st = LabelStyle {
        family: val_family,
        size: require("font.size", ax_y.labels.font.size)?,
        pad: require("padding", ax_y.labels.padding)?,
        limit: ax_y.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT),
        separation: ax_y.labels.min_gap.unwrap_or(0.0),
        flush: flush_threshold(ax_y.labels.flush.as_ref())?,
        angle: vega_fit::normalize_angle(ax_y.labels.angle.unwrap_or(0.0)),
    };
    let (off_x, off_y) = (ax_x.ticks.offset.unwrap_or(0.0), ax_y.ticks.offset.unwrap_or(0.0));

    // An authored domain pins the scale (a point scale reads the pair as its two
    // category values). A horizontal axis reaches 0 (`zero: true`); a descending
    // horizontal pair is not forwarded.
    let point_labels: Option<Vec<String>> = pins_point_domain.map(|pins| match ax_y.authored_continuous_domain() {
        Some((lo, hi)) if pins => std::iter::once(lo).chain((hi != lo).then_some(hi)).map(num_full).collect(),
        _ => Vec::new(),
    });
    let stacked_wide = !horizontal && head.stacked() && !head.wide_measures().is_empty();
    let rail = if facet.is_none() && (matches!(family, "line" | "area") || stacked_wide) {
        head.rail_reserve(fonts, payload.width)?
    } else {
        0.0
    };
    let rail_reserve = if matches!(family, "line" | "area") || stacked_wide { rail } else { 0.0 };
    // A stacked wide fold's labels pile at the baseline and spread `gap_px` apart through the
    // slope of a probe, so the shared y scale spans exactly that spread, niced.
    let rail_labels = if stacked_wide && rail > 0.0 {
        head.rail_labels(&payload.series_palette(head.palette()), fonts, payload.width)?
    } else {
        None
    };
    let rail_domain = |plot_h: f64| -> Option<(f64, f64)> {
        rail_labels.as_ref().map(|rail| {
            if head.normalized() {
                return (0.0, 1.0);
            }
            let spread = rail.gap_px * (rail.series.len() - 1) as f64 / plot_h;
            crate::families::cartesian::d3_nice_domain(0.0, spread, 10)
        })
    };
    let pinned_static = match &point_labels {
        Some(labels) => (!labels.is_empty()).then_some((0.0, labels.len() as f64)),
        None => ax_y.authored_continuous_domain().and_then(|(lo, hi)| {
            let (lo, hi) = crate::families::cartesian::authored_domain_pair(ax_y, lo, hi);
            match (horizontal, lo > hi) {
                (false, _) => Some((lo, hi)),
                (true, true) => None,
                (true, false) => Some((lo.min(0.0), hi.max(0.0))),
            }
        }),
    };
    let pinned_cell = Cell::new(rail_domain(payload.height).or(pinned_static));
    // An authored `scale.values` bakes the ladder; Vega draws the rungs inside the domain.
    let baked = point_labels.is_none() && !ax_y.tick_values.is_empty();
    let has_rungs = pinned_cell.get().is_some() || baked || zero_rung;
    let tick_len = if has_rungs { ax_y.tick_len() } else { 0.0 };
    // A pinned domain gives `measure_axis_to_vl` its numbers, so a right axis keeps its
    // labels right-aligned in a measured gutter; with none they grow away from the plot.
    let away = side_right && (moved || point_labels.is_some() || ax_y.labels.align.is_none() || (pinned_cell.get().is_none() && !baked));
    let base_pad = if moved { 2.0 } else { st.pad };
    let gutter = if side_right && !away && baked {
        let measure_family = measure::stand_in_family(st.family);
        let drawn = format_tick_labels(&crate::common::drawn_tick_values(ax_y), ax_y, false, if horizontal { "x" } else { "y" })?;
        let widest = drawn.iter().try_fold(0.0_f64, |acc, s| fonts.measure(s, measure_family, st.size).map(|w| acc.max(w)))?;
        widest.min(st.limit) + VALUE_AXIS_BREATHING_ROOM
    } else if side_right && !away {
        crate::common::estimated_tick_label_gutter(
            fonts,
            st.family,
            st.size,
            st.limit,
            ax_y.tick_label.as_ref(),
            ax_y.labels.format.as_ref(),
            ax_y,
            f64::INFINITY,
            f64::NEG_INFINITY,
        )?
    } else {
        base_pad
    };

    // A position channel left unset has no field to title its axis.
    let cat_text = match &extra.x {
        Some(x) => crate::common::axis_title_text(x, extra.x_label.as_deref(), &extra.field_labels)?,
        None => String::new(),
    };
    let val_text = match (extra.y_label.as_deref(), &extra.y) {
        (None, _) if !head.wide_measures().is_empty() => crate::common::wide_measures_title(head.wide_measures(), &extra.field_labels)?,
        (label, Some(y)) => crate::common::axis_title_text(y, label, &extra.field_labels)?,
        (_, None) => String::new(),
    };
    let primary_title_style = if dual_titles && ax_y.title.visible == Some(false) && extra.y_label.is_none() {
        crate::common::vega_default_axis_title(ax_y.title.font.family.clone())
    } else {
        ax_y.title.clone()
    };
    let primary_title = title_of(family, "axis_y", &primary_title_style, val_text, fonts)?;
    let cat_title = title_of(family, "axis_x", &ax_x.title, cat_text, fonts)?;

    let mut primary = ValueAxisPlan {
        horizontal,
        side,
        offset: off_y,
        point: point_labels.is_some(),
        domain: pinned_cell.get().unwrap_or((0.0, 0.0)),
        rungs: Vec::new(),
        labels: Vec::new(),
        widths: Vec::new(),
        tick_len,
        pad: base_pad,
        labels_shown: ax_y.labels.visible.unwrap_or(true) && has_rungs,
        away,
        gutter,
        title: primary_title,
        line: domain_line(ax_y, family, "axis_y")?,
        grid: ax_y.grid.visible.unwrap_or(false) && facet.is_none(),
        ghost: false,
    };
    // Each layer's own axis beside the base's, overlaying it on a shared side.
    let mut layer_axes: Vec<ValueAxisPlan> = Vec::new();
    if !horizontal && facet.is_none() && (ysides.is_some() || family == "scatter") {
        let zero_label = format_tick_labels(&[0.0], ax_y, false, "y")?.remove(0);
        let zero_width = fonts.measure_shaped(&zero_label, st.family, st.size)?;
        for (i, layer) in layers.iter().enumerate() {
            let layer_side = ysides.as_ref().map_or(side, |s| if s.layers[i] == LayerSide::Right { 1.0 } else { -1.0 });
            let has_rung = match layer {
                OverlayLayer::Scatter(_) => false,
                OverlayLayer::Bar(_) => true,
                OverlayLayer::Line(l) => l.area_mark.is_some() || family != "bar",
            };
            let text = match &layer.axis_y().title {
                Some(authored) => authored.clone(),
                None => crate::common::axis_title_text(layer.y(), layer.label(), &extra.field_labels)?,
            };
            let style = if dual_titles && ax_y.title.visible == Some(false) { crate::common::vega_default_axis_title(ax_y.title.font.family.clone()) } else { ax_y.title.clone() };
            let grid = layer.axis_y().grid.as_ref().and_then(|g| g.visible).or(ax_y.grid.visible).unwrap_or(false);
            let tick_len = if has_rung { ax_y.tick_len() } else { 0.0 };
            layer_axes.push(ValueAxisPlan {
                horizontal: false,
                side: layer_side,
                offset: off_y,
                point: !has_rung,
                domain: (0.0, 0.0),
                rungs: if has_rung { vec![0.0] } else { Vec::new() },
                labels: if has_rung { vec![zero_label.clone()] } else { Vec::new() },
                widths: if has_rung { vec![zero_width] } else { Vec::new() },
                tick_len,
                pad: st.pad,
                labels_shown: ax_y.labels.visible.unwrap_or(true) && has_rung,
                away: layer_side > 0.0,
                gutter: st.pad,
                title: title_of(family, "axis_y", &style, text, fonts)?,
                line: domain_line(ax_y, family, "axis_y")?,
                grid: grid && has_rung,
                ghost: false,
            });
        }
    }
    let category = CategoryAxis {
        horizontal,
        side,
        offset: off_x,
        title: cat_title,
        line: domain_line(ax_x, family, "axis_x")?,
    };

    // The ladder at a tick count, as rungs, their drawn labels and widths.
    let ladder_at = |count: usize| -> Result<(Vec<f64>, Vec<String>, Vec<f64>), String> {
        let normalized_rail = rail_labels.is_some() && head.normalized();
        let rungs: Vec<f64> = match (&point_labels, pinned_cell.get(), baked, has_rungs) {
            _ if normalized_rail => crate::families::bar::NORMALIZE_QUARTILE_TICKS.to_vec(),
            (Some(labels), ..) => (0..labels.len()).map(|i| i as f64 + 0.5).collect(),
            (None, Some((lo, hi)), true, _) => crate::families::cartesian::ladder_within_domain(&ax_y.tick_values, lo, hi),
            (None, None, true, _) => ax_y.tick_values.clone(),
            (None, Some((lo, hi)), false, _) => crate::families::cartesian::unbaked_ticks(ax_y, lo, hi, count),
            (None, None, false, true) => vec![0.0],
            (None, None, false, false) => Vec::new(),
        };
        let texts = match &point_labels {
            Some(labels) => labels.clone(),
            None => format_tick_labels(&rungs, ax_y, normalized_rail, if horizontal { "x" } else { "y" })?,
        };
        let labels = texts
            .iter()
            .map(|text| crate::common::truncate_label(text, st.limit, st.family, st.size, fonts, true))
            .collect::<Result<Vec<_>, _>>()?;
        let widths = labels
            .iter()
            .map(|label| fonts.measure_shaped(label, st.family, st.size))
            .collect::<Result<Vec<_>, _>>()?;
        Ok((rungs, labels, widths))
    };

    // The channels keep their legends over zero values: each title still paints
    // and each box is as wide as its title. The legends stack down a side edge
    // and along a top or bottom one, except a shape legend, which sits right of
    // the plot unless the edge is the right strip.
    let legends = head.legends(family, &extra.legend, &payload.series_palette(head.palette()), head.stacked() && !horizontal)?;
    if legends.iter().any(|lg| lg.kind != LegendKind::Shape) {
        for (what, font) in [("legend.label", &extra.legend.label.font), ("legend.title", &extra.legend.title.font)] {
            crate::families::empty::require_font(family, what, font)?;
        }
    }
    let legend_entries: Vec<Vec<LegendEntry>> = legends.iter().map(|lg| lg.entries()).collect();
    // A shape legend keeps Vega-Lite's default title and label style.
    let legend_boxes = legends
        .iter()
        .zip(&legend_entries)
        .map(|(lg, entries)| match lg.kind {
            LegendKind::Shape => ShapeLegend::new(fonts, payload, lg.title.clone(), Vec::new()).map(LegendBox::Shape),
            LegendKind::Color | LegendKind::Size => {
                vega_legend(entries, lg.mixed_marks, &lg.title, &extra.legend, fonts).map(LegendBox::Vega)
            }
        })
        .collect::<Result<Vec<_>, _>>()?;
    let legend_edge = extra.legend.position.edge.as_str();
    let stack_vertical = matches!(legend_edge, "left" | "right");
    let right_strip = legend_edge == "right" && !extra.legend.position.overlay;
    let stacked: Vec<usize> = (0..legends.len()).filter(|&i| legends[i].kind != LegendKind::Shape || right_strip).collect();
    let (stack_offsets, stack_size) = stack_legends(
        &stacked.iter().map(|&i| legend_boxes[i].size()).collect::<Vec<_>>(),
        !stack_vertical,
    );
    let loose_shape = (0..legends.len()).find(|&i| !stacked.contains(&i));

    let legend_xy = Cell::new((0.0, 0.0));
    let shape_xy = Cell::new((0.0, 0.0));

    // The axes a facet over no panels keeps: those beside the (missing) rows of a
    // columns facet, or under the (missing) columns of a rows facet.
    let axes_for = |primary: &ValueAxisPlan| -> Vec<ValueAxisPlan> {
        let mirrored = !horizontal && pins_point_domain.is_none() && matches!(family, "bar" | "line" | "area") && head.wide_measures().is_empty();
        let mut axes = vec![primary.clone()];
        axes.extend(layer_axes.iter().cloned());
        if facet == Some(Facet::Columns) && mirrored {
            // The footer copy sits `offset` past the header's own.
            axes[0].offset *= 2.0;
            let mut ghost = primary.clone();
            ghost.side = -ghost.side;
            ghost.offset = 0.0;
            ghost.away = true;
            ghost.title = None;
            ghost.ghost = true;
            axes.push(ghost);
        }
        axes
    };
    let value_axes_drawn = |facet: Option<Facet>| match facet {
        None => true,
        Some(Facet::Columns) => !horizontal,
        Some(Facet::Rows) => horizontal,
        Some(Facet::Both) => false,
    };
    let category_drawn = |facet: Option<Facet>| match facet {
        None => true,
        Some(Facet::Columns) => horizontal,
        Some(Facet::Rows) => !horizontal,
        Some(Facet::Both) => false,
    };

    // One layout pass over the plot size: the axes' ink, the legends' placement.
    // The axis beside the plot joins Vega's `xBounds`, the bottom axis `yBounds`.
    let rail_widths: Vec<f64> = match &rail_labels {
        Some(rail) => rail
            .series
            .iter()
            .map(|(name, _)| fonts.measure(name, &rail.font.0, rail.font.1))
            .collect::<Result<_, _>>()?,
        None => Vec::new(),
    };
    let ink_pass = |axes: &[ValueAxisPlan], pw: f64, ph: f64, b: &mut vega_fit::Bounds| {
        // The rail's labels hang off the plot's right edge at their series' height.
        if let (Some(rail), Some((_, top))) = (&rail_labels, rail_domain(ph)) {
            for (i, &w) in rail_widths.iter().enumerate() {
                let y = ph * (1.0 - rail.gap_px * i as f64 / ph / top);
                b.union(&vega_fit::text_ink(pw + rail.offset, y, w, rail.font.1, "left", "middle"));
            }
        }
        let (mut side_axis, mut bottom_axis): (Option<vega_fit::Bounds>, Option<vega_fit::Bounds>) = (None, None);
        let join = |acc: &mut Option<vega_fit::Bounds>, ink: vega_fit::Bounds| match acc {
            Some(seen) => seen.union(&ink),
            None => *acc = Some(ink),
        };
        if value_axes_drawn(facet) {
            for axis in axes {
                join(if horizontal { &mut bottom_axis } else { &mut side_axis }, axis.ink(&st, pw, ph));
            }
        }
        if category_drawn(facet) {
            join(if horizontal { &mut side_axis } else { &mut bottom_axis }, category.ink(pw, ph));
        }
        for ink in side_axis.iter().chain(&bottom_axis) {
            b.union(ink);
        }
        let x_bounds = side_axis.map_or((0.0, pw), |a| (a.x1.min(0.0), a.x2.max(pw)));
        let y2 = bottom_axis.map_or(ph, |a| a.y2.max(ph));
        if !stacked.is_empty() {
            let origin = legend_origin(&extra.legend.position, x_bounds, (0.0, y2), (pw, ph), stack_size);
            legend_xy.set(origin);
            // A legend joins a plot's view by its edge alone; a facet's root takes the whole group.
            if facet.is_some() && !extra.legend.position.overlay {
                b.union(&vega_fit::Bounds::new(origin.0, origin.1, origin.0 + stack_size.0, origin.1 + stack_size.1));
            } else if let Some(reach) = legend_fit_bounds(&extra.legend.position, origin, stack_size) {
                b.union(&reach);
            }
        }
        if let Some(i) = loose_shape {
            let x = x_bounds.1.ceil() + VEGA_LEGEND_OFFSET;
            shape_xy.set((x, 0.0));
            b.union(&vega_fit::Bounds::new(x, 0.0, x + legend_boxes[i].size().0, legend_boxes[i].size().1));
        }
    };

    // The plot a facet lays out under `autosize: pad`: its rect is the panels' own, only
    // the height still fits the card.
    let facet_plot = |facet: Facet| -> Result<(f64, f64), String> {
        let panel_w = crate::families::multiples::panel_width(payload, 1, false, 0.0, crate::ViewScale::Discrete)?;
        Ok(match facet {
            Facet::Columns => (0.0, payload.height),
            Facet::Rows => (panel_w, 0.0),
            Facet::Both => (0.0, 0.0),
        })
    };

    let widest_of = |axes: &[ValueAxisPlan]| -> f64 { axes.iter().flat_map(|a| a.widths.iter().copied()).fold(0.0, f64::max) };
    // Production declares the concat's plot width before the shared scale exists, over a
    // [0, 1] domain's ladder; the labels the real scale draws then overshoot the view by their
    // extra width.
    let rail_gutter_estimate = if rail_labels.is_some() {
        let rungs = if head.normalized() {
            crate::families::bar::NORMALIZE_QUARTILE_TICKS.to_vec()
        } else {
            crate::families::cartesian::unbaked_ticks(ax_y, 0.0, 1.0, vl_default_tick_count(payload.height))
        };
        format_tick_labels(&rungs, ax_y, head.normalized(), "y")?
            .iter()
            .map(|label| fonts.measure_shaped(label, st.family, st.size))
            .collect::<Result<Vec<_>, _>>()?
            .into_iter()
            .fold(0.0, f64::max)
    } else {
        0.0
    };
    let fit = |axes: &[ValueAxisPlan]| -> Result<(vega_fit::FitMargins, (f64, f64)), String> {
        match facet {
            None => {
                let mut ink = |pw: f64, ph: f64, b: &mut vega_fit::Bounds| ink_pass(axes, pw, ph, b);
                let mut m = frame.fit_beside(rail_reserve, &title, &mut ink);
                let mut canvas = (payload.width, payload.height);
                if let Some(rail) = &rail_labels {
                    // The concat's plot is declared a fixed chrome short of the card and the
                    // view overshoots by what the chrome really takes.
                    m.plot_w = payload.width - rail_reserve - (frame.pad.0 + m.left) - frame.pad.1
                        + (widest_of(axes).ceil() - rail_gutter_estimate.ceil());
                    // Likewise the height keeps a fixed chrome estimate of `fontSize + 32`.
                    m.plot_h = payload.height - rail.font.1 - 32.0;
                    canvas.0 = (frame.pad.0 + m.left + m.plot_w + rail.offset + rail.pane + frame.pad.1).ceil();
                    canvas.1 = frame.pad.2 + m.top + m.plot_h + m.bottom + frame.pad.3;
                }
                Ok((m, canvas))
            }
            Some(kind) => {
                let (pw, mut ph) = facet_plot(kind)?;
                let (pad_l, pad_r, pad_t, pad_b) = frame.pad;
                let avail_h = payload.height - pad_t - pad_b;
                let mut m = vega_fit::pad_plot(pw, ph, |pw, ph, b| ink_pass(axes, pw, ph, b), title.ink());
                if kind == Facet::Columns {
                    for _ in 0..4 {
                        let next = (avail_h - m.top - m.bottom).max(0.0);
                        if next == ph {
                            break;
                        }
                        ph = next;
                        m = vega_fit::pad_plot(pw, ph, |pw, ph, b| ink_pass(axes, pw, ph, b), title.ink());
                    }
                }
                let canvas = (pad_l + m.left + pw + m.right + pad_r, pad_t + m.top + ph + m.bottom + pad_b);
                Ok((m, canvas))
            }
        }
    };

    let (mut axes, mut margins, mut canvas);
    // The default `tickCount`, `ceil(extent/40)`, reads the plot extent the ladder itself
    // helps size: iterate to the fixed point.
    let extent_of = |m: &vega_fit::FitMargins| if horizontal { m.plot_w } else { m.plot_h };
    {
        let (rungs, labels, widths) = ladder_at(vl_default_tick_count(if horizontal { payload.width } else { payload.height }))?;
        (primary.rungs, primary.labels, primary.widths) = (rungs, labels, widths);
        axes = axes_for(&primary);
        (margins, canvas) = fit(&axes)?;
        if pinned_cell.get().is_some() && !baked {
            for _ in 0..4 {
                let domain = rail_domain(margins.plot_h).or(pinned_cell.get());
                pinned_cell.set(domain);
                let moved_domain = primary.domain != domain.unwrap_or((0.0, 0.0));
                primary.domain = domain.unwrap_or((0.0, 0.0));
                let (rungs, labels, widths) = ladder_at(vl_default_tick_count(extent_of(&margins)))?;
                if rungs == primary.rungs && !moved_domain {
                    break;
                }
                (primary.rungs, primary.labels, primary.widths) = (rungs, labels, widths);
                axes = axes_for(&primary);
                (margins, canvas) = fit(&axes)?;
            }
        }
    }

    let m = &margins;
    let framed = frame.sized(canvas.0, canvas.1);
    let (plot_left, plot_top) = framed.origin(m);
    let mut svg = String::new();
    framed.open(&mut svg);
    title.paint(&mut svg, m.title_pos, (plot_left, plot_top));
    paint_axes(&mut svg, family, &axes, &category, &st, (ax_x, ax_y), (plot_left, plot_top), m, horizontal, value_axes_drawn(facet), category_drawn(facet))?;
    if let Some(rail) = &rail_labels {
        let (_, top) = pinned_cell.get().expect("a rail pins the scale");
        let (family, size, weight, style) = &rail.font;
        let _ = write!(svg, "<g transform=\"translate({:.3},{plot_top:.3})\">", plot_left + m.plot_w + rail.offset);
        for (i, (name, ink)) in rail.series.iter().enumerate() {
            let value = rail.gap_px * i as f64 / m.plot_h;
            let y = m.plot_h * (1.0 - value / top) + measure::baseline_shift("middle", *size);
            let _ = write!(
                svg,
                "<text x=\"0\" y=\"{y:.3}\" text-anchor=\"start\" font-family=\"{}\" font-size=\"{size}\" font-weight=\"{}\" font-style=\"{}\" fill=\"{}\">{}</text>",
                vega_escape_attr(family),
                xml_escape_attr(weight),
                xml_escape_attr(style),
                xml_escape_attr(ink),
                xml_escape(name),
            );
        }
        svg.push_str("</g>");
    }

    let (stack_x, stack_y) = legend_xy.get();
    let (slide_x, slide_y) = if stacked.is_empty() {
        (0.0, 0.0)
    } else {
        slide_block(payload.chart_id()?, &extra.legend.position, stack_size, (m.plot_w, m.plot_h))?
    };
    let (stack_x, stack_y) = (stack_x + slide_x, stack_y + slide_y);
    for (&i, &offset) in stacked.iter().zip(&stack_offsets) {
        let (dx, dy) = if stack_vertical { (0.0, offset) } else { (offset, 0.0) };
        legend_boxes[i].paint(&mut svg, &legend_entries[i], &extra.legend, plot_left + stack_x + dx, plot_top + stack_y + dy);
    }
    if let Some(i) = loose_shape {
        let (x, y) = shape_xy.get();
        legend_boxes[i].paint(&mut svg, &legend_entries[i], &extra.legend, plot_left + x, plot_top + y);
    }
    svg.push_str("</svg>");
    Ok(svg)
}

/// Paints the axes: domain lines, gridlines, ticks, labels and titles.
#[allow(clippy::too_many_arguments)]
fn paint_axes(
    svg: &mut String,
    family: &str,
    axes: &[ValueAxisPlan],
    category: &CategoryAxis,
    st: &LabelStyle,
    (ax_x, ax_y): (&AxisStyle, &AxisStyle),
    (plot_left, plot_top): (f64, f64),
    m: &vega_fit::FitMargins,
    horizontal: bool,
    paint_value_axes: bool,
    paint_category: bool,
) -> Result<(), String> {
    let grid_dash = match ax_y.grid.dash.as_deref() {
        None | Some([]) => String::new(),
        Some(dash) => format!(" stroke-dasharray=\"{}\"", dash.iter().map(f64::to_string).collect::<Vec<_>>().join(",")),
    };
    let grid_opacity = ax_y.grid.opacity.filter(|&o| o != 1.0).map_or(String::new(), |o| format!(" stroke-opacity=\"{o}\""));
    let line = |svg: &mut String, (x1, y1, x2, y2): (f64, f64, f64, f64), color: &str, width: f64| {
        let _ = write!(
            svg,
            "<line x1=\"{x1:.2}\" y1=\"{y1:.2}\" x2=\"{x2:.2}\" y2=\"{y2:.2}\" stroke=\"{color}\" stroke-width=\"{width}\"/>"
        );
    };
    let axis_x0 = |axis: &ValueAxisPlan| if axis.side > 0.0 { m.plot_w + axis.offset + 0.5 } else { 0.5 - axis.offset };
    // Gridlines belong to the plot; the axes' own ink follows in their groups.
    let _ = write!(svg, "<g transform=\"translate({plot_left:.2},{plot_top:.2})\">");
    if paint_value_axes {
        for axis in axes.iter().filter(|axis| axis.grid) {
            for &value in &axis.rungs {
                let at = axis.pos(value, m.plot_w, m.plot_h).round() + 0.5;
                let ends = if horizontal {
                    format!("x1=\"{at:.2}\" y1=\"0.5\" x2=\"{at:.2}\" y2=\"{:.2}\"", m.plot_h + 0.5)
                } else {
                    format!("x1=\"0.5\" y1=\"{at:.2}\" x2=\"{:.2}\" y2=\"{at:.2}\"", m.plot_w + 0.5)
                };
                let _ = write!(
                    svg,
                    "<line {ends} stroke=\"{}\" stroke-width=\"{}\"{grid_dash}{grid_opacity}/>",
                    xml_escape_attr(ax_y.grid.color.as_deref().ok_or_else(|| format!("{family}: axis_y.grid.color absent (theme required)"))?),
                    ax_y.grid.width.ok_or_else(|| format!("{family}: axis_y.grid.width absent (theme required)"))?
                );
            }
        }
    }
    svg.push_str("</g>");

    // Vega's description of an axis over this empty scale: no category, or the degenerate domain.
    let described = |title: &Option<Title>, style: &AxisTitle, axis: &AxisStyle, scale: CaptionScale| match title {
        Some(t) => AxisCaption::new(&t.text, &t.style, scale),
        None => AxisCaption::new("", style, scale),
    }
    .bare_if_undrawn(axis);
    let no_values = |axis: &AxisStyle| -> Result<CaptionScale, String> {
        Ok(CaptionScale::Discrete { values: Vec::new(), format: vega_axis_format(axis, false)? })
    };

    let paint_value = |svg: &mut String, axis: &ValueAxisPlan| -> Result<(), String> {
        let scale = if axis.point {
            CaptionScale::Discrete { values: axis.labels.clone(), format: vega_axis_format(ax_y, false)? }
        } else {
            // A scale with no rung folds an extent over nothing: Vega's inverted infinite domain.
            let domain = if axis.rungs.is_empty() { (f64::INFINITY, f64::NEG_INFINITY) } else { axis.domain };
            CaptionScale::Numeric { kind: NumericScale::of_axis(ax_y)?, domain, format: vega_axis_format(ax_y, true)? }
        };
        let caption = described(&axis.title, &ax_y.title, ax_y, scale);
        let edge = if horizontal {
            (plot_left + 0.5, plot_top + m.plot_h + axis.offset + 0.5)
        } else {
            (plot_left + axis_x0(axis), plot_top)
        };
        let channel = if horizontal { AxisChannel::X } else { AxisChannel::Y };
        crate::contract::axis_group_open(svg, channel, &if axis.ghost { caption.untitled() } else { caption }, edge)?;
        if let Some((width, color)) = &axis.line {
            let ends = if horizontal {
                let y = plot_top + m.plot_h + axis.offset + 0.5;
                (plot_left + 0.5, y, plot_left + m.plot_w + 0.5, y)
            } else {
                let x = plot_left + axis_x0(axis);
                (x, plot_top + 0.5, x, plot_top + m.plot_h + 0.5)
            };
            line(svg, ends, color, *width);
        }
        if axis.tick_len > 0.0 {
            let ticks = &ax_y.ticks;
            for &value in &axis.rungs {
                let at = axis.pos(value, m.plot_w, m.plot_h).round() + 0.5;
                let (x1, y1, x2, y2) = if horizontal {
                    let y = plot_top + m.plot_h + axis.offset + 0.5;
                    (plot_left + at, y, plot_left + at, y + axis.tick_len)
                } else {
                    let x = plot_left + axis_x0(axis);
                    (x, plot_top + at, x + axis.side * axis.tick_len, plot_top + at)
                };
                let _ = write!(
                    svg,
                    "<line x1=\"{x1:.2}\" y1=\"{y1:.2}\" x2=\"{x2:.2}\" y2=\"{y2:.2}\" stroke=\"{}\" stroke-width=\"{}\"/>",
                    xml_escape_attr(ticks.color.as_deref().ok_or_else(|| format!("{family}: axis_y.ticks.color absent (theme required)"))?),
                    ticks.width.ok_or_else(|| format!("{family}: axis_y.ticks.width absent (theme required)"))?
                );
            }
        }
        if axis.labels_shown {
            let visible = axis.label_inks(st, m.plot_w, m.plot_h).1;
            for ((&value, label), _) in axis.rungs.iter().zip(&axis.labels).zip(&visible).filter(|(_, &v)| v) {
                let along = axis.pos(value, m.plot_w, m.plot_h);
                let (align, baseline) = axis.anchor(st, along, m.plot_w);
                let text_anchor = match align {
                    "left" => "start",
                    "right" => "end",
                    _ => "middle",
                };
                let shift = measure::baseline_shift(baseline, st.size);
                let (anchor_x, anchor_y) = if horizontal {
                    (plot_left + 0.5 + along, plot_top + m.plot_h + axis.offset + 0.5 + axis.tick_len + axis.pad)
                } else {
                    (plot_left + axis_x0(axis) + axis.label_dx(), plot_top + along + 0.5)
                };
                let position = if st.angle == 0.0 {
                    format!("x=\"{anchor_x:.2}\" y=\"{:.2}\"", anchor_y + shift)
                } else {
                    format!("transform=\"translate({anchor_x:.2},{anchor_y:.2}) rotate({}) translate(0,{shift})\"", st.angle)
                };
                let _ = write!(
                    svg,
                    "<text {position} text-anchor=\"{text_anchor}\" font-family=\"{}\" \
                     font-size=\"{}\" font-weight=\"{}\" fill=\"{}\">{}</text>",
                    vega_escape_attr(st.family),
                    st.size,
                    xml_escape_attr(&ax_y.labels.font.weight_str()),
                    xml_escape_attr(
                        ax_y.labels.font.color.as_deref().ok_or_else(|| format!("{family}: axis_y.labels.font.color absent (theme required)"))?
                    ),
                    xml_escape(label)
                );
            }
        }
        if let Some(t) = &axis.title {
            let reach = axis.reach(st);
            if horizontal {
                crate::common::emit_axis_title_at(
                    svg,
                    &t.text,
                    &t.style,
                    plot_left + m.plot_w / 2.0,
                    plot_top + m.plot_h + axis.offset + 0.5 + reach + t.pad + measure::baseline_shift("top", t.size),
                    None,
                );
            } else {
                crate::common::emit_axis_title_rotated(
                    svg,
                    &t.text,
                    &t.style,
                    plot_left + axis_x0(axis) + axis.side * reach,
                    plot_top + m.plot_h / 2.0,
                    axis.side,
                    None,
                );
            }
        }
        crate::contract::axis_group_close(svg);
        Ok(())
    };

    let paint_category_axis = |svg: &mut String| -> Result<(), String> {
        let caption = described(&category.title, &ax_x.title, ax_x, no_values(ax_x)?);
        let edge = if horizontal {
            let x = if category.side > 0.0 { m.plot_w + category.offset + 0.5 } else { 0.5 - category.offset };
            (plot_left + x, plot_top)
        } else {
            (plot_left + 0.5, plot_top + m.plot_h + category.offset + 0.5)
        };
        let channel = if horizontal { AxisChannel::Y } else { AxisChannel::X };
        crate::contract::axis_group_open(svg, channel, &caption, edge)?;
        if let Some((width, color)) = &category.line {
            let ends = if horizontal {
                let x = plot_left + if category.side > 0.0 { m.plot_w + category.offset + 0.5 } else { 0.5 - category.offset };
                (x, plot_top + 0.5, x, plot_top + m.plot_h + 0.5)
            } else {
                let y = plot_top + m.plot_h + category.offset + 0.5;
                (plot_left + 0.5, y, plot_left + m.plot_w + 0.5, y)
            };
            line(svg, ends, color, *width);
        }
        if let Some(t) = &category.title {
            if horizontal {
                let x = if category.side > 0.0 { m.plot_w + category.offset + 0.5 } else { 0.5 - category.offset };
                crate::common::emit_axis_title_rotated(svg, &t.text, &t.style, plot_left + x, plot_top + m.plot_h / 2.0, category.side, None);
            } else {
                crate::common::emit_axis_title_at(
                    svg,
                    &t.text,
                    &t.style,
                    plot_left + m.plot_w / 2.0,
                    plot_top + m.plot_h + category.offset + 0.5 + t.pad + measure::baseline_shift("top", t.size),
                    None,
                );
            }
        }
        crate::contract::axis_group_close(svg);
        Ok(())
    };

    // Vega lists the x axis before the y axis.
    if !horizontal && paint_category {
        paint_category_axis(svg)?;
    }
    if paint_value_axes {
        for axis in axes {
            paint_value(svg, axis)?;
        }
    }
    if horizontal && paint_category {
        paint_category_axis(svg)?;
    }
    Ok(())
}
