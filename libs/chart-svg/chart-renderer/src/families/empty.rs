//! A chart whose query returned no rows.
//!
//! Production draws each family's zero-row chart through that family's own
//! path, and the results differ by family: a table paints its "No data"
//! text, a KPI raises `ChartDataError` (the board paints the error card), and
//! a Vega-Lite family compiles over an empty dataset, so every scale is
//! degenerate and its axes, marks and legends come out empty. A family is
//! ported here only once its empty drawing is; every other family refuses by
//! name, never draws its ordinary chart over nothing.

use serde::Deserialize;
use serde_json::Value;

use crate::common::{AxisStyle, Font, TitleStyle};
use crate::contract::stamp_view_root;
use crate::diagnostic::Diagnostic;
use crate::families::{bar, kpi, pie, spark_bar, table};
use crate::families::legend::{
    emit_vega_legend, legend_title_text, refuse_slide_blocker, vega_legend, LegendConfig,
    LegendSymbol, VEGA_LEGEND_OFFSET,
};
use crate::frame::{ChartFrame, FrameTitle};
use crate::text::FontBook;
use crate::vega_fit;
use crate::Payload;

pub fn render(chart_type: &str, payload: &Payload, fonts: &FontBook) -> Result<String, Diagnostic> {
    Ok(match chart_type {
        "bar" => bar::render_empty(payload, fonts).and_then(stamp_view_root)?,
        "line" | "area" => render_inkless(chart_type, payload, fonts)?,
        "scatter" | "heatmap" => render_discrete(chart_type, payload, fonts).and_then(stamp_view_root)?,
        "pie" => pie::render_empty(payload, fonts).and_then(stamp_view_root)?,
        "table" => table::render_empty(payload, fonts)?,
        // A KPI has no empty drawing: no rows is `ERR-KPI-NO-DATA`.
        "kpi" => kpi::render(payload, fonts)?,
        // `spark_bar.py::_validate_spark_bar_value_field` returns on `not data`
        // (`spark_bar.py:109`) and the render paints no bar rows: its ordinary
        // path is the empty chart, with nothing to decide here.
        "spark_bar" => spark_bar::render(payload, fonts)?,
        other => return Err(format!("{other}: empty result set not supported").into()),
    })
}

/// The resolved fields every zero-row Vega-Lite cartesian chart reads. A query
/// with no rows leaves the fields that sample the data null, so the family
/// models cannot deserialize it.
#[derive(Deserialize)]
pub(crate) struct Head {
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    title: Option<String>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    subtitle: Option<String>,
    title_style: Option<TitleStyle>,
    pub(crate) background: String,
    #[serde(default)]
    color: Option<String>,
    #[serde(default)]
    stack: Option<String>,
    #[serde(default)]
    layers: Vec<Value>,
    #[serde(default)]
    multiples: Option<Value>,
    #[serde(default)]
    support_table: Option<Value>,
    #[serde(default)]
    wide_measures: Vec<String>,
    pub(crate) style: HeadStyle,
}

#[derive(Deserialize)]
pub(crate) struct HeadStyle {
    pub(crate) axis_x: AxisStyle,
    pub(crate) axis_y: AxisStyle,
    #[serde(default)]
    title_font: Option<Font>,
}

/// The shapes a family's zero-row drawing covers beyond the plain single
/// series, which [`Head::refuse_unmodeled`] would refuse.
#[derive(Clone, Copy, Default)]
pub(crate) struct Allow {
    pub(crate) axis_titles: bool,
    pub(crate) color: bool,
}

impl Head {
    pub(crate) fn parse(family: &str, resolved: &Value) -> Result<Head, String> {
        serde_json::from_value(resolved.clone()).map_err(|e| format!("{family} payload: {e}"))
    }

    /// Refuses every shape whose zero-row drawing differs from a single
    /// series over a plain, unpinned value axis: a color channel (its legend),
    /// a stack, layers, small multiples, a support table, wide measures, a
    /// pinned value domain, a visible axis line and (unless allowed) visible
    /// axis titles.
    pub(crate) fn refuse_unmodeled(&self, family: &str, allow: Allow) -> Result<(), String> {
        let refused = [
            (self.color.is_some() && !allow.color, "a color channel"),
            (self.stack.as_deref().is_some_and(|s| s != "none"), "a stack"),
            (!self.layers.is_empty(), "layers"),
            (self.multiples.is_some(), "small multiples"),
            (self.support_table.is_some(), "a support table"),
            (!self.wide_measures.is_empty(), "wide measures"),
        ];
        if let Some((_, what)) = refused.iter().find(|(on, _)| *on) {
            return Err(format!("{family}: empty result set not supported with {what}"));
        }
        let (ax_x, ax_y) = (&self.style.axis_x, &self.style.axis_y);
        let pinned = ax_y.authored_continuous_domain().is_some()
            || ax_y.has_baked_edge()
            || !ax_y.tick_values.is_empty()
            || ax_y.is_zero_false()
            || ax_y.is_log_scale()
            || !matches!(&ax_y.mirror, None | Some(Value::Null) | Some(Value::Bool(false)));
        if pinned {
            return Err(format!("{family}: empty result set not supported with a pinned value domain"));
        }
        // Vega draws an axis' domain line even over an empty scale; the
        // zero-row painters draw none.
        if ax_x.line_visible() || ax_y.line_visible() {
            return Err(format!("{family}: empty result set not supported with a visible axis line"));
        }
        if !allow.axis_titles && (ax_y.title.visible.unwrap_or(true) || ax_x.title.visible.unwrap_or(true)) {
            return Err(format!("{family}: empty result set not supported with axis titles"));
        }
        Ok(())
    }

    /// The color channel's field, when one is authored.
    pub(crate) fn color(&self) -> Option<&str> {
        self.color.as_deref()
    }

    /// The chart title, wrapped against the frame's padded width.
    pub(crate) fn title(&self, frame: &ChartFrame, fonts: &FontBook) -> Result<FrameTitle, String> {
        FrameTitle::new(
            self.title.as_deref(),
            self.subtitle.as_deref(),
            self.style.title_font.as_ref(),
            self.title_style.as_ref(),
            frame.avail_w(),
            fonts,
        )
    }
}

/// The resolved theme always carries a font's size, family and color; a
/// payload without them is a stale one, not a chart to draw with guessed
/// values.
fn require_font<'a>(family: &str, what: &str, font: &'a Font) -> Result<(f64, &'a str), String> {
    let size = font.size.ok_or_else(|| format!("{family}: {what}.font.size absent (theme required)"))?;
    let name = font.family.as_deref().ok_or_else(|| format!("{family}: {what}.font.family absent (theme required)"))?;
    font.color.as_ref().ok_or_else(|| format!("{family}: {what}.font.color absent (theme required)"))?;
    Ok((size, name))
}

/// A shown axis title's font and padding, all theme-required.
fn require_title_theme<'a>(
    family: &str,
    axis: &str,
    title: &'a crate::common::AxisTitle,
) -> Result<(f64, &'a str, f64), String> {
    let (size, name) = require_font(family, &format!("{axis}.title"), &title.font)?;
    let pad = title.padding.ok_or_else(|| format!("{family}: {axis}.title.padding absent (theme required)"))?;
    Ok((size, name, pad))
}

/// The value axis side of a zero-row cartesian chart.
pub(crate) fn value_axis_on_right(family: &str, ax_y: &AxisStyle) -> Result<bool, String> {
    match ax_y.position.as_deref() {
        Some("right") => Ok(true),
        Some("left") => Ok(false),
        other => Err(format!("{family}: axis_y.position {other:?} not supported — only \"left\"/\"right\"")),
    }
}

/// The view ink of the two axis groups with nothing drawn in them: each group
/// still sits `offset` off the plot edge and that point joins the view bounds
/// (the value axis' at the top of the plot, the category axis' at its
/// bottom-left), so a fractional offset moves the margins and the title.
pub(crate) fn union_axis_groups(
    b: &mut vega_fit::Bounds,
    (pw, ph): (f64, f64),
    (off_x, off_y): (f64, f64),
    val_on_right: bool,
) {
    let value_x = if val_on_right { pw + off_y } else { -off_y };
    b.union(&vega_fit::Bounds::new(value_x, 0.0, value_x, 0.0));
    b.union(&vega_fit::Bounds::new(0.0, ph + off_x, 0.0, ph + off_x));
}

/// A line or area over no rows: production compiles the layered marks over an
/// empty dataset, so Vega draws empty mark groups and two empty axes — the
/// value scale's domain is `Infinity to −Infinity`, its axis has no rung, and
/// the category axis has no value — leaving the title block as the only ink.
///
/// Nothing is painted on the plot rect, and `vega_fit::title_layout` places the
/// title group from the view bounds' top and left edges alone: the plot rect
/// and the two empty axis groups (`union_axis_groups`) are the only ink, so a
/// left value axis with an `offset` moves the title left of the plot edge.
fn render_inkless(family: &str, payload: &Payload, fonts: &FontBook) -> Result<String, String> {
    let head = Head::parse(family, &payload.resolved)?;
    head.refuse_unmodeled(family, Allow::default())?;
    let frame = ChartFrame::of(payload, &head.background);
    let title = head.title(&frame, fonts)?;
    let val_on_right = value_axis_on_right(family, &head.style.axis_y)?;
    let offsets = (
        head.style.axis_x.ticks.offset.unwrap_or(0.0),
        head.style.axis_y.ticks.offset.unwrap_or(0.0),
    );
    let mut ink = |pw: f64, ph: f64, b: &mut vega_fit::Bounds| {
        union_axis_groups(b, (pw, ph), offsets, val_on_right);
    };
    let m = frame.fit(family, &title, &mut ink)?;
    let mut svg = String::new();
    frame.open(&mut svg);
    title.paint(&mut svg, m.title_pos, frame.origin(&m));
    svg.push_str("</svg>");
    Ok(svg)
}

/// A scatter or heatmap over no rows: without a data sample both channels are
/// inferred nominal, so Vega compiles two point/band-scale axes over zero
/// values — no label, tick or gridline. The ink left is the chart title, the
/// axis titles a scatter shows (a heatmap hides them) and the symbol legend of
/// a color channel, which keeps its title over zero entries.
///
/// The x title hangs `padding + baseline_shift("top")` below the axis, which
/// sits `offset` under the plot; the y title, rotated, hangs `padding` (+ the
/// rotated frame's `translate(0, −2)`) right of the value axis. Both extend
/// the fit by the same `padding + fontSize + …` terms the populated arms
/// reserve (`bar.rs`'s `cat_title_extra`/`val_title_extra`).
fn render_discrete(family: &str, payload: &Payload, fonts: &FontBook) -> Result<String, String> {

    #[derive(Deserialize)]
    struct Extra {
        x: String,
        y: String,
        #[serde(default)]
        x_label: Option<String>,
        #[serde(default)]
        y_label: Option<String>,
        field_labels: crate::common::FieldLabels,
        #[serde(default)]
        size: Option<String>,
        #[serde(default)]
        shape: Option<Value>,
        legend: LegendConfig,
    }
    let head = Head::parse(family, &payload.resolved)?;
    let extra: Extra = serde_json::from_value(payload.resolved.clone())
        .map_err(|e| format!("{family} payload: {e}"))?;
    head.refuse_unmodeled(family, Allow { axis_titles: true, color: true })?;
    if extra.size.is_some() || extra.shape.as_ref().is_some_and(|v| !v.is_null()) {
        return Err(format!("{family}: empty result set not supported with a size or shape channel"));
    }
    let (ax_x, ax_y) = (&head.style.axis_x, &head.style.axis_y);
    let val_on_right = value_axis_on_right(family, ax_y)?;
    let (off_x, off_y) = (ax_x.ticks.offset.unwrap_or(0.0), ax_y.ticks.offset.unwrap_or(0.0));
    let x_text = crate::common::axis_title_text(&extra.x, extra.x_label.as_deref(), &extra.field_labels)?;
    let y_text = crate::common::axis_title_text(&extra.y, extra.y_label.as_deref(), &extra.field_labels)?;
    let x_shown = crate::common::axis_title_band(&ax_x.title, &x_text) > 0.0;
    let y_shown = crate::common::axis_title_band(&ax_y.title, &y_text) > 0.0;
    if y_shown {
        require_title_theme(family, "axis_y", &ax_y.title)?;
    }
    let (x_fs, x_pad, x_w) = if x_shown {
        let (fs, family_x, pad) = require_title_theme(family, "axis_x", &ax_x.title)?;
        (fs, pad, fonts.measure(&x_text, family_x, fs)?)
    } else {
        (0.0, 0.0, 0.0)
    };
    if y_shown && !val_on_right {
        return Err(format!("{family}: empty result set not supported with a titled left value axis"));
    }
    let y_extra = if y_shown { crate::common::y_title_extra(&ax_y.title, &y_text) } else { 0.0 };

    // A color channel keeps its symbol legend over zero values: the title
    // still paints and the box is as wide as the title (`vega_legend`'s
    // `ceil(max(title, entries))`), hung `VEGA_LEGEND_OFFSET` right of the
    // axis chrome's unshifted right edge.
    let legend = match head.color() {
        Some(field) if extra.legend.visible => {
            refuse_slide_blocker(&extra.legend.position, family, Some("is not modeled on an empty result set"))?;
            if extra.legend.position.edge != "right" {
                return Err(format!("{family}: empty result set not supported with a legend not on the right"));
            }
            for (what, font) in [
                ("legend.label", &extra.legend.label.font),
                ("legend.title", &extra.legend.title.font),
            ] {
                require_font(family, what, font)?;
            }
            let title = legend_title_text(field, &extra.legend, &extra.field_labels)?;
            Some(vega_legend(&[], &title, &extra.legend, fonts)?)
        }
        _ => None,
    };
    let frame = ChartFrame::of(payload, &head.background);
    let title = head.title(&frame, fonts)?;
    // The value axis chrome's right edge, unshifted by the crisp 0.5 (a left
    // axis leaves the plot's own edge).
    let right_edge = |pw: f64| if val_on_right { pw + off_y + y_extra } else { pw };
    let legend_x = |pw: f64| right_edge(pw).ceil() + VEGA_LEGEND_OFFSET;
    let mut ink = |pw: f64, ph: f64, b: &mut vega_fit::Bounds| {
        let y_axis_x = if val_on_right { pw + off_y } else { -off_y };
        b.union(&vega_fit::Bounds::new(y_axis_x, 0.0, right_edge(pw), ph));
        b.union(&vega_fit::Bounds::new(0.0, ph + off_x, 0.0, ph + off_x));
        if let Some(lg) = &legend {
            // Only the legend's horizontal reach can extend the view: it hangs
            // from the plot's top edge and its height stays inside the plot.
            let x = legend_x(pw);
            b.union(&vega_fit::Bounds::new(x, 0.0, x + lg.box_w, 0.0));
        }
        if x_shown {
            b.union(&vega_fit::text_ink(pw / 2.0, ph + off_x + x_pad, x_w, x_fs, "center", "top"));
        }
    };
    let m = frame.fit(family, &title, &mut ink)?;
    let (plot_left, plot_top) = frame.origin(&m);
    let mut svg = String::new();
    frame.open(&mut svg);
    title.paint(&mut svg, m.title_pos, (plot_left, plot_top));
    // Both titles sit in axis groups carrying Vega's +0.5 crisp translate; the
    // shared emitters add it to the along-axis coordinate.
    crate::common::emit_axis_title_at(
        &mut svg,
        &x_text,
        &ax_x.title,
        plot_left + m.plot_w / 2.0,
        plot_top + m.plot_h + off_x + 0.5 + x_pad + vega_fit::baseline_shift("top", x_fs),
        None,
    );
    crate::common::emit_axis_title_rotated(
        &mut svg,
        &y_text,
        &ax_y.title,
        plot_left + m.plot_w + 0.5 + off_y,
        plot_top + m.plot_h / 2.0,
        1.0,
        None,
    );
    if let Some(lg) = &legend {
        emit_vega_legend(
            &mut svg,
            &[],
            lg,
            &extra.legend,
            LegendSymbol::Circle,
            plot_left + legend_x(m.plot_w),
            plot_top,
            false,
        );
    }
    svg.push_str("</svg>");
    Ok(svg)
}
