//! Scatter's temporal x axis: a UTC time scale over the chart's distinct
//! dates (`type: temporal` + a calendar `timeUnit`, `padding: 0`), so a point
//! sits at its calendar position rather than on an even category step. The
//! label vocabulary, cadence, tilt and parity are the shared temporal ladder
//! (`temporal::resolve_vega_x_labels`) line/area already run; this module
//! only adds the scatter-side geometry — where each opener lands on a plot of
//! width `pw` and how its ink joins the fit — and the paint.
//!
//! Baked once per (date domain, crowding width), then queried per candidate
//! plot width by the fit and painted at the final one, either inside a single
//! chart's plot group or, for a small-multiples composite, in the shared
//! footer band.

use std::fmt::Write as _;

use crate::common::AxisStyle;
use crate::families::line::{flush_align, flush_anchor, tilted_temporal_label};
use crate::families::temporal::{
    detect_time_unit, parse_authored_time_unit, parse_iso_date, resolve_vega_x_labels, tooltip_header_date, utc_domain_extent,
    utc_fraction_at, TimeUnit,
};
use crate::svg::{vega_escape_attr, xml_escape, xml_escape_attr};
use crate::text::FontBook;
use crate::vega_fit::{self, Bounds};

#[derive(Clone)]
struct AxisLabel {
    frac: f64,
    /// Row one, or the inline `"2024 Jan"` text once tilted.
    text: String,
    /// The flat two-row label's context row (`"2024"` under `"Jan"`).
    row2: Option<String>,
    /// Measured advance of the widest drawn row.
    width: f64,
    /// `vega_fit::text_ink`'s alignment vocabulary for a rotated label; a flat
    /// label flushes at a range end and centers otherwise.
    tilted_align: &'static str,
    baseline: &'static str,
}

#[derive(Clone)]
pub(crate) struct TemporalXAxis {
    data_tu: TimeUnit,
    extent: (i64, i64),
    angle: f64,
    /// An authored `labels.align` wins over every derived anchor.
    align: Option<String>,
    parity: bool,
    labels: Vec<AxisLabel>,
    font_size: f64,
    family: String,
    color: String,
    weight: String,
    label_pad: f64,
    label_limit: f64,
    separation: f64,
    tick_len: f64,
    ticks_visible: bool,
    tick_color: String,
    tick_width: f64,
    /// `axis.offset`; the drawn axis group adds its own half-pixel crisp
    /// translate on top (`axis_drop`).
    axis_offset: f64,
    title_text: String,
    title_key: Option<crate::contract::LabelKey>,
}

impl TemporalXAxis {
    /// `dates` is the chart's distinct ISO dates, ascending. Refuses, by
    /// name, every shape the shared ladder does not price for a scatter: a
    /// grain the calendar cadence cannot detect, any grain but year, quarter
    /// and month (day and week grains floor their vertices to a period start
    /// while their openers stay raw), and the authored label controls the
    /// ladder leaves to Vega.
    pub(crate) fn resolve(
        dates: &[String],
        ax_x: &AxisStyle,
        usable_width: f64,
        title_text: String,
        title_key: Option<crate::contract::LabelKey>,
        fonts: &FontBook,
    ) -> Result<Self, String> {
        let refs: Vec<&str> = dates.iter().map(String::as_str).collect();
        let data_tu = detect_time_unit(&refs)
            .ok_or("scatter: a date x with no detectable calendar cadence is not supported")?;
        if !matches!(data_tu, TimeUnit::Year | TimeUnit::YearQuarter | TimeUnit::YearMonth) {
            return Err(format!(
                "scatter: a date x at the {data_tu:?} grain is not supported — only year, \
                 quarter and month starts"
            ));
        }
        let labels_cfg = &ax_x.labels;
        // An authored grain that re-buckets the dates would move every point to
        // its bucket start; only the grain the data already sits on is modeled.
        let authored_tu = ax_x.time_unit.as_ref().filter(|v| !v.is_null());
        if authored_tu.is_some_and(|v| v.as_str().and_then(parse_authored_time_unit) != Some(data_tu)) {
            return Err("scatter: an authored axis_x.time_unit other than the data's own grain is not supported".into());
        }
        crate::families::temporal::refuse_authored_time_format("scatter", labels_cfg.spec())?;
        crate::families::temporal::refuse_grain_format("scatter", &ax_x.grain_formats, data_tu)?;
        if ax_x.fiscal_year_start_month != 1 && data_tu.is_fiscal_sensitive() {
            return Err(format!(
                "scatter: a date x at the {data_tu:?} grain under a non-calendar \
                 fiscal_year_start_month is not supported: production resolves this shape \
                 ordinal and measures one band per row, which this port's continuous \
                 temporal axis does not model"
            ));
        }
        let extent = utc_domain_extent(dates).ok_or("scatter: a date x needs two distinct dates")?;

        let font_size = labels_cfg.font.size_or(11.0);
        let family = labels_cfg.font.family_or("sans-serif").to_string();
        let overlap = labels_cfg.overlap.as_ref();
        let mut measure = |t: &str| labels_cfg.font.measure_weighted(fonts, t, font_size);
        let resolved = resolve_vega_x_labels(
            dates,
            data_tu,
            labels_cfg.time_unit.as_deref(),
            usable_width,
            labels_cfg.flush.as_ref() != Some(&serde_json::Value::Bool(false)),
            true, // continuous_temporal
            ax_x.fiscal_year_start_month,
            overlap.is_some_and(|o| o.skip),
            overlap.is_some_and(|o| o.tilt),
            labels_cfg.tilt_increments.as_deref(),
            font_size,
            None,
            &mut measure,
        )?;
        // An authored angle bypasses the tilt ladder and rotates every label.
        let angle = labels_cfg.angle.unwrap_or(resolved.angle);
        let mut labels = Vec::with_capacity(resolved.labels.len());
        for tl in &resolved.labels {
            let (text, rows, tilted_align, baseline) = tilted_temporal_label(tl, angle, resolved.format_tu);
            let mut width = fonts.measure(&text, &family, font_size)?;
            if rows == 2 {
                width = width.max(fonts.measure(&tl.row2, &family, font_size)?);
            }
            labels.push(AxisLabel {
                frac: utc_fraction_at(&dates[tl.domain_idx], extent)
                    .expect("a resolved opener is a parsed domain date"),
                text,
                row2: (rows == 2).then(|| tl.row2.clone()),
                width,
                tilted_align,
                baseline,
            });
        }
        let ticks = &ax_x.ticks;
        Ok(Self {
            data_tu,
            extent,
            angle,
            align: labels_cfg.align.clone(),
            parity: resolved.parity,
            labels,
            font_size,
            family,
            color: labels_cfg.font.color.as_deref().unwrap_or("#626366").to_string(),
            weight: labels_cfg.font.weight_str(),
            label_pad: labels_cfg.padding.unwrap_or(6.0),
            label_limit: labels_cfg.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT),
            separation: labels_cfg.min_gap.unwrap_or(0.0),
            tick_len: ticks.length.unwrap_or(6.0),
            ticks_visible: ticks.visible.unwrap_or(true),
            tick_color: ticks.color.as_deref().unwrap_or("#D0D3D9").to_string(),
            tick_width: ticks.width.unwrap_or(1.0),
            axis_offset: ticks.offset.unwrap_or(0.0),
            title_text,
            title_key,
        })
    }

    /// The scale position of `date` as a fraction of the plot width.
    pub(crate) fn frac(&self, date: &str) -> Option<f64> {
        utc_fraction_at(date, self.extent)
    }

    /// A point's tooltip x value: the full bucket at the data grain
    /// (`"Jan 2024"`), the header the structured tooltip formats a date with.
    pub(crate) fn tooltip_date(&self, date: &str) -> String {
        let (y, m, d) = parse_iso_date(date).expect("a plotted date parsed at resolve");
        tooltip_header_date(y, m, d, Some(self.data_tu))
    }

    /// Plot-width fractions of every opener — the ticks and gridlines, which
    /// Vega keeps at each opener whether or not parity later hides its label.
    pub(crate) fn tick_fracs(&self) -> Vec<f64> {
        self.labels.iter().map(|l| l.frac).collect()
    }

    fn tick_eff(&self) -> f64 {
        if self.ticks_visible { self.tick_len } else { 0.0 }
    }

    /// The label anchor's row below the plot bottom, before the axis group's
    /// half pixel.
    fn label_top(&self) -> f64 {
        self.axis_offset + self.tick_eff() + self.label_pad
    }

    fn align(&self, l: &AxisLabel) -> &str {
        match self.align.as_deref() {
            Some(authored) => authored,
            None if self.angle != 0.0 => l.tilted_align,
            None => flush_align(l.frac),
        }
    }

    /// `text-anchor` for [`Self::align`]'s vocabulary.
    fn anchor(&self, l: &AxisLabel) -> &'static str {
        match self.align(l) {
            "left" => "start",
            "right" => "end",
            _ => "middle",
        }
    }

    fn ink(&self, l: &AxisLabel, pw: f64, row_y: f64) -> Bounds {
        let x = pw * l.frac;
        let mut ink = vega_fit::text_ink(x, row_y, l.width.min(self.label_limit), self.font_size, self.align(l), l.baseline);
        if l.row2.is_some() {
            ink.y2 += self.font_size + 2.0;
        }
        if self.angle != 0.0 {
            ink = ink.rotate(self.angle, x, row_y);
        }
        ink
    }

    /// Vega's runtime `labelOverlap: parity` over the labels' real ink at plot
    /// width `pw`; every label survives when the ladder did not request it.
    fn kept(&self, pw: f64) -> Vec<bool> {
        if !self.parity {
            return vec![true; self.labels.len()];
        }
        let ink: Vec<Bounds> = self.labels.iter().map(|l| self.ink(l, pw, 0.0)).collect();
        vega_fit::parity_visible(&ink, self.separation)
    }

    /// Joins the surviving labels' ink into `b` for a `pw` × `ph` plot.
    pub(crate) fn union_label_ink(&self, pw: f64, ph: f64, b: &mut Bounds) {
        let row_y = ph + self.label_top();
        for (l, keep) in self.labels.iter().zip(self.kept(pw)) {
            if keep {
                b.union(&self.ink(l, pw, row_y));
            }
        }
    }

    /// Ticks, labels and title, positioned against a plot at `plot_left` of
    /// width `plot_w` whose bottom edge is `plot_bottom`.
    pub(crate) fn paint(&self, svg: &mut String, ax_x: &AxisStyle, plot_left: f64, plot_w: f64, plot_bottom: f64) {
        let fs = self.font_size;
        // The drawn axis group is translated by half a pixel so 1px strokes
        // land on a pixel boundary; the fit measures the ink before it.
        let axis_drop = self.axis_offset + 0.5;
        crate::contract::axis_group_open(svg, crate::contract::AxisChannel::X, (plot_left + 0.5, plot_bottom + axis_drop));
        if self.ticks_visible {
            for l in &self.labels {
                let gx = plot_left + (plot_w * l.frac).round() + 0.5;
                let _ = write!(
                    svg,
                    "<line x1=\"{gx:.3}\" y1=\"{:.3}\" x2=\"{gx:.3}\" y2=\"{:.3}\" \
                     stroke=\"{}\" stroke-width=\"{}\"/>",
                    plot_bottom + axis_drop,
                    plot_bottom + axis_drop + self.tick_len,
                    xml_escape_attr(&self.tick_color),
                    self.tick_width
                );
            }
        }
        let kept = self.kept(plot_w);
        let anchor_y = plot_bottom + axis_drop + self.tick_eff() + self.label_pad;
        let flat_y = anchor_y + vega_fit::baseline_shift("top", fs);
        let mut reach = 0.0_f64;
        for (l, keep) in self.labels.iter().zip(&kept) {
            if !keep {
                continue;
            }
            reach = reach.max(self.ink(l, plot_w, 0.0).y2);
            let gx = plot_left + plot_w * l.frac + 0.5;
            if self.angle != 0.0 {
                let text_anchor = self.anchor(l);
                let rows = match &l.row2 {
                    Some(row2) => format!(
                        "<tspan>{}</tspan><tspan x=\"0\" dy=\"{}\">{}</tspan>",
                        xml_escape(&l.text),
                        fs + 2.0,
                        xml_escape(row2)
                    ),
                    None => xml_escape(&l.text),
                };
                let _ = write!(
                    svg,
                    "<text text-anchor=\"{text_anchor}\" transform=\"translate({gx:.3},{anchor_y:.3}) \
                     rotate({}) translate(0,{})\" font-family=\"{}\" font-size=\"{fs}\" \
                     font-weight=\"{}\" fill=\"{}\">{rows}</text>",
                    self.angle,
                    vega_fit::baseline_shift(l.baseline, fs),
                    vega_escape_attr(&self.family),
                    xml_escape_attr(&self.weight),
                    xml_escape_attr(&self.color),
                );
                continue;
            }
            let anchor = if self.align.is_some() { self.anchor(l) } else { flush_anchor(l.frac) };
            let mut row = |y: f64, text: &str| {
                let _ = write!(
                    svg,
                    "<text x=\"{gx:.3}\" y=\"{y:.3}\" text-anchor=\"{anchor}\" font-family=\"{}\" \
                     font-size=\"{fs}\" font-weight=\"{}\" fill=\"{}\">{}</text>",
                    vega_escape_attr(&self.family),
                    xml_escape_attr(&self.weight),
                    xml_escape_attr(&self.color),
                    xml_escape(text)
                );
            };
            row(flat_y, &l.text);
            if let Some(row2) = &l.row2 {
                row(flat_y + fs + 2.0, row2);
            }
        }
        // Vega's `axisTitleLayout`: the title clears the label ink's own
        // reach past the anchor, then `titlePadding`.
        let title_fs = ax_x.title.font.size_or(11.0);
        let title_y = anchor_y + reach + ax_x.title.padding.unwrap_or(10.0) + vega_fit::baseline_shift("top", title_fs);
        crate::common::emit_axis_title_at(svg, &self.title_text, &ax_x.title, plot_left + plot_w / 2.0, title_y, self.title_key);
        crate::contract::axis_group_close(svg);
    }
}
