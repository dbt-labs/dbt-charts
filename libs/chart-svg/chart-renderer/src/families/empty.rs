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
use crate::families::bar;
use crate::families::empty_cartesian::{self, Facet, ValueAxis};
use crate::families::{kpi, pie, spark_bar, table};
use crate::families::layers::{
    claim_layers, merged_legend_title, LayerScale, OverlayBarLayer, OverlayLayer, OverlayLineLayer, OverlayScatterLayer,
};
use crate::families::legend::{legend_title_text, LegendConfig, LegendEntry, LegendSymbol};
use crate::frame::{ChartFrame, FrameTitle};
use measure::FontBook;
use crate::Payload;

/// A zero-row chart's root, printed and stamped as the view root.
fn printed_view_root(root: scene::Node) -> Result<String, String> {
    stamp_view_root(print::print(&[root]))
}

pub fn render(chart_type: &str, payload: &Payload, fonts: &FontBook) -> Result<String, Diagnostic> {
    Ok(match chart_type {
        "bar" => printed_view_root(bar::render_empty(payload, fonts)?)?,
        "line" | "area" => printed_view_root(empty_cartesian::render(
            chart_type,
            payload,
            fonts,
            ValueAxis::Linear { horizontal: false, zero_rung: false },
        )?)?,
        // A wide scatter folds its measures into one quantitative value, a linear scale with no rung.
        "scatter" if wide_measures(payload) => printed_view_root(empty_cartesian::render(
            chart_type,
            payload,
            fonts,
            ValueAxis::Linear { horizontal: false, zero_rung: false },
        )?)?,
        "scatter" | "heatmap" => printed_view_root(empty_cartesian::render(
            chart_type,
            payload,
            fonts,
            ValueAxis::Point { pins_domain: chart_type == "scatter" },
        )?)?,
        "pie" => stamp_view_root(pie::render_empty(payload, fonts)?)?,
        // `table::bind` decides its own empty result.
        "table" => table::render(payload, fonts)?,
        // A KPI has no empty drawing: no rows is `ERR-KPI-NO-DATA`.
        "kpi" => kpi::render(payload, fonts)?,
        // `spark_bar.py::_validate_spark_bar_value_field` returns on `not data`
        // (`spark_bar.py:109`) and the render paints no bar rows: its ordinary
        // path is the empty chart, with nothing to decide here.
        "spark_bar" => spark_bar::render(payload, fonts)?,
        other => return Err(crate::refuse!("{other}: empty result set not supported").into()),
    })
}

fn is_top_oriented(position: &crate::families::legend::LegendPosition) -> bool {
    position.edge == "top" || (position.overlay && matches!(position.edge.as_str(), "left" | "right"))
}

fn wide_measures(payload: &Payload) -> bool {
    payload.resolved.get("wide_measures").and_then(Value::as_array).is_some_and(|m| !m.is_empty())
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
    resolved_channels: std::collections::HashMap<String, Channel>,
    /// The authored `color:` field; a wide fold's own series field is not it.
    #[serde(default)]
    color: Option<String>,
    #[serde(default)]
    stack: Option<String>,
    #[serde(default)]
    layers: Vec<Value>,
    #[serde(default)]
    wide_measures: Vec<String>,
    #[serde(default)]
    multiples: Option<crate::families::multiples::MultiplesConfig>,
    #[serde(default)]
    palette: Vec<String>,
    field_labels: crate::common::FieldLabels,
    #[serde(default)]
    y: Option<String>,
    #[serde(default)]
    y_label: Option<String>,
    #[serde(default)]
    size: Option<String>,
    #[serde(default)]
    shape: Option<String>,
    pub(crate) style: HeadStyle,
}

#[derive(Deserialize)]
struct Channel {
    mode: String,
    data_field: Option<String>,
}

#[derive(Deserialize)]
pub(crate) struct HeadStyle {
    pub(crate) axis_x: AxisStyle,
    pub(crate) axis_y: AxisStyle,
    #[serde(default)]
    endpoint_labels: Option<EndpointLabels>,
    #[serde(default)]
    series_label: Option<SeriesLabel>,
    #[serde(default)]
    single_series_fill: Option<String>,
    #[serde(default)]
    title_font: Option<Font>,
}

#[derive(Deserialize)]
struct SeriesLabel {
    font_family: String,
    font_size: f64,
    font_weight: String,
    #[serde(default)]
    font_style: Option<String>,
    #[serde(default)]
    dark_companion_palette: Vec<String>,
    #[serde(default)]
    gap_px: Option<f64>,
}

/// A stacked wide fold's endpoint-label rail over no rows: the series bottom to top, each in
/// its companion ink, spaced `gap_px` apart.
pub(crate) struct RailLabels {
    pub(crate) series: Vec<(String, String)>,
    /// The label pane's width before it rounds up to whole pixels.
    pub(crate) pane: f64,
    pub(crate) gap_px: f64,
    pub(crate) offset: f64,
    pub(crate) font: (String, f64, String, String),
}

#[derive(Deserialize)]
struct EndpointLabels {
    visible: bool,
    label_offset: f64,
}

impl Head {
    /// The width a line or area's endpoint-label rail takes off the plot: the
    /// series it names (a wide fold's measures; a color channel has no values
    /// yet) plus its edge gap, `label_offset` away from the plot.
    pub(crate) fn rail_reserve(&self, fonts: &FontBook, chart_w: f64) -> Result<f64, String> {
        let (el, sl) = self.rail_style()?;
        if self.color().is_none() || !el.visible {
            return Ok(0.0);
        }
        // A wide fold split by a color dimension names a series per dimension value: none without rows.
        let mut series = if self.color.is_some() { Vec::new() } else { self.wide_measures.clone() };
        series.sort();
        let labels = display_text::wide_measure_labels_for(&self.wide_measures, &self.field_labels)?;
        let names: Vec<String> = series.iter().map(|s| crate::common::wide_series_display(s, &labels)).collect();
        let pane = crate::families::cartesian::rail_pane_width(&names, &sl.font_family, sl.font_size, &sl.font_weight, fonts, chart_w)?;
        // `_correct_concat_overshoot` works in whole pixels: the pane's width rounds up.
        Ok(pane.ceil() + el.label_offset)
    }

    fn rail_style(&self) -> Result<(&EndpointLabels, &SeriesLabel), String> {
        let el = self.style.endpoint_labels.as_ref().ok_or("zero-row rail: style.endpoint_labels absent")?;
        let sl = self.style.series_label.as_ref().ok_or("zero-row rail: style.series_label absent")?;
        Ok((el, sl))
    }

    /// The rail a stacked wide fold keeps over no rows, `None` when the labels are off.
    pub(crate) fn rail_labels(&self, series_palette: &[String], fonts: &FontBook, chart_w: f64) -> Result<Option<RailLabels>, String> {
        let (el, sl) = self.rail_style()?;
        if self.color().is_none() || !el.visible || self.wide_measures.is_empty() {
            return Ok(None);
        }
        let gap_px = sl.gap_px.ok_or("zero-row rail: series_label.gap_px absent (theme required)")?;
        let labels = display_text::wide_measure_labels_for(&self.wide_measures, &self.field_labels)?;
        let mut domain = self.wide_measures.clone();
        domain.sort();
        let series = self
            .wide_measures
            .iter()
            .map(|measure| {
                let ink = crate::common::series_ink(measure, &domain, series_palette, &sl.dark_companion_palette, &[], None)?;
                Ok((crate::common::wide_series_display(measure, &labels), ink))
            })
            .collect::<Result<Vec<_>, String>>()?;
        let font = (sl.font_family.clone(), sl.font_size, sl.font_weight.clone(), sl.font_style.clone().unwrap_or_else(|| "normal".into()));
        let mut names: Vec<String> = series.iter().map(|(name, _)| name.clone()).collect();
        names.sort();
        let pane = crate::families::cartesian::rail_pane_width(&names, &sl.font_family, sl.font_size, &sl.font_weight, fonts, chart_w)?;
        Ok(Some(RailLabels { series, pane, gap_px, offset: el.label_offset, font }))
    }

    pub(crate) fn parse(family: &str, resolved: &Value) -> Result<Head, String> {
        Deserialize::deserialize(resolved).map_err(|e| format!("{family} payload: {e}"))
    }

    /// The chart's own palette.
    pub(crate) fn palette(&self) -> &[String] {
        &self.palette
    }

    /// The wide fold's measures, empty for a chart that is not folded.
    pub(crate) fn wide_measures(&self) -> &[String] {
        &self.wide_measures
    }

    /// The chart's authored layers, parsed.
    pub(crate) fn overlay_layers(&self, family: &str) -> Result<Vec<OverlayLayer>, String> {
        self.layers
            .iter()
            .map(|raw| match raw.get("type").and_then(Value::as_str) {
                Some("line" | "area") => OverlayLineLayer::parse(raw, family).map(|l| OverlayLayer::Line(Box::new(l))),
                Some("scatter") => OverlayScatterLayer::parse(raw, family).map(|l| OverlayLayer::Scatter(Box::new(l))),
                Some("bar") => OverlayBarLayer::parse(raw, family).map(|l| OverlayLayer::Bar(Box::new(l))),
                other => Err(format!("{family}: layer type {other:?} is none of line, area, scatter or bar")),
            })
            .collect()
    }

    /// Which panels a small-multiples chart over no rows lacks, `None` for a plain chart.
    pub(crate) fn facet(&self) -> Option<Facet> {
        let mc = self.multiples.as_ref()?;
        Some(match (mc.rows.is_some(), mc.columns.is_some()) {
            (true, true) => Facet::Both,
            (true, false) => Facet::Rows,
            (false, _) => Facet::Columns,
        })
    }

    /// Whether the stack runs to a whole of 1 (`stack: normalize`).
    pub(crate) fn normalized(&self) -> bool {
        self.stack.as_deref() == Some("normalize")
    }

    /// Whether the chart stacks its measure.
    pub(crate) fn stacked(&self) -> bool {
        self.stack.as_deref().is_some_and(|s| s != "none")
    }

    /// The field the color channel splits series by (a wide fold's synthetic
    /// label field included), unless the color is a literal.
    pub(crate) fn color(&self) -> Option<&str> {
        let channel = self.resolved_channels.get("color").filter(|c| c.mode != "literal")?;
        channel.data_field.as_deref().filter(|f| !f.is_empty())
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
pub(crate) fn require_font<'a>(family: &str, what: &str, font: &'a Font) -> Result<(f64, &'a str), String> {
    let size = font.size.ok_or_else(|| format!("{family}: {what}.font.size absent (theme required)"))?;
    let name = font.family.as_deref().ok_or_else(|| format!("{family}: {what}.font.family absent (theme required)"))?;
    font.color.as_ref().ok_or_else(|| format!("{family}: {what}.font.color absent (theme required)"))?;
    Ok((size, name))
}

/// A shown axis title's font and padding, all theme-required.
pub(crate) fn require_title_theme<'a>(
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


/// The base mark's glyph in a zero-row legend, which a layer's own entries
/// sit beside.
#[derive(Clone, Copy)]
enum BaseGlyph {
    Square,
    Circle,
    /// A stacked wide area's circle, ringed in the canvas.
    RingedCircle,
    FilledCircle,
    ShortStroke,
    Stroke,
}

/// The channel a legend keys, which decides where it stacks.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum LegendKind {
    Color,
    Size,
    Shape,
}

/// A zero-row chart's legend: what its color, wide and layer channels name
/// without any data to draw entries from.
pub(crate) struct EmptyLegend<'a> {
    pub(crate) kind: LegendKind,
    pub(crate) title: String,
    base: Vec<(String, String)>,
    scale: Option<LayerScale>,
    glyph: BaseGlyph,
    canvas: &'a str,
    scatter: Option<ScatterLegend>,
    /// A bar's swatches are stroked in the canvas unless a line layer mixes strokes into the legend.
    canvas_squares: bool,
    /// A plain line layer turns on the mixed stroke legend.
    pub(crate) mixed_marks: bool,
}

/// What a scatter's layered legend does beyond a bar's: a categorical y leaves the base marks
/// uncolored, so only a scatter or bar layer lists an entry, and no stroke legend means no
/// layer's glyph carries a stroke either.
struct ScatterLegend {
    mixed: bool,
    bar_borders: std::collections::HashMap<String, String>,
    /// The entries of the line layers a categorical y leaves unlisted.
    unlisted: std::collections::HashSet<String>,
}

impl<'a> EmptyLegend<'a> {
    fn title_only(kind: LegendKind, title: String, canvas: &'a str) -> Self {
        EmptyLegend { kind, title, base: Vec::new(), scale: None, glyph: BaseGlyph::Circle, canvas, scatter: None, canvas_squares: false, mixed_marks: false }
    }

    /// The entries in legend order, each with its own glyph.
    pub(crate) fn entries(&self) -> Vec<LegendEntry<'_>> {
        fn glyph<'x>(kind: BaseGlyph, fill: &'x str, canvas: &'x str) -> LegendSymbol<'x> {
            match kind {
                BaseGlyph::Square => LegendSymbol::Square(fill),
                BaseGlyph::Circle => LegendSymbol::Circle,
                BaseGlyph::RingedCircle => LegendSymbol::BorderedCircle(canvas),
                BaseGlyph::FilledCircle => LegendSymbol::FilledCircle,
                BaseGlyph::ShortStroke => LegendSymbol::ShortStroke,
                BaseGlyph::Stroke => LegendSymbol::Stroke(2.0),
            }
        }
        let entries: Vec<LegendEntry> = match &self.scale {
            Some(scale) => scale.legend_entries(&self.base, |fill| glyph(self.glyph, fill, self.canvas)),
            // A bar's swatches over no layers are stroked in the canvas.
            None => self
                .base
                .iter()
                .map(|(label, fill)| LegendEntry {
                    label,
                    fill,
                    symbol: Some(glyph(self.glyph, self.canvas, self.canvas)),
                    series_key: None,
                    recede: None,
                })
                .collect(),
        };
        let entries: Vec<LegendEntry> = if self.canvas_squares {
            entries.into_iter().map(|mut e| { e.symbol = Some(LegendSymbol::Square(self.canvas)); e }).collect()
        } else {
            entries
        };
        let Some(rules) = &self.scatter else { return entries };
        entries
            .into_iter()
            .map(|mut entry| {
                entry.symbol = match entry.symbol {
                    Some(LegendSymbol::Square(_)) if rules.bar_borders.contains_key(entry.label) => {
                        Some(LegendSymbol::Square(&rules.bar_borders[entry.label]))
                    }
                    Some(LegendSymbol::Square(_)) if !rules.mixed => Some(LegendSymbol::FilledSquare),
                    Some(LegendSymbol::FadedCircle(_)) if !rules.mixed => Some(glyph(self.glyph, entry.fill, self.canvas)),
                    other => other,
                };
                entry
            })
            .filter(|entry| !rules.unlisted.contains(entry.label))
            .collect()
    }
}

impl Head {
    /// The legends a zero-row chart of `family` draws, in registration order
    /// (color, size, shape). Vega lays a legend with no entries out at
    /// `-Number.MAX_VALUE`, which moves a top-oriented one's whole view to
    /// `translate(…, Infinity)`; production drops those (`drop_empty_legends`).
    pub(crate) fn legends<'a>(
        &'a self,
        family: &str,
        cfg: &LegendConfig,
        series_palette: &[String],
        stack_up: bool,
    ) -> Result<Vec<EmptyLegend<'a>>, Diagnostic> {
        let mut legends = Vec::new();
        if let Some(color) = self.color_legend(family, cfg, series_palette, stack_up)? {
            legends.push(color);
        }
        let position = &cfg.position;
        let top_oriented = is_top_oriented(position);
        if !cfg.visible || top_oriented {
            return Ok(legends);
        }
        let title_of = |field: &str| -> Result<String, String> {
            if cfg.title.visible == Some(false) {
                return Ok(String::new());
            }
            crate::families::legend::color_channel_title(field, cfg, &self.field_labels)
        };
        if let Some(field) = self.size.as_deref() {
            legends.push(EmptyLegend::title_only(LegendKind::Size, title_of(field)?, &self.background));
        }
        // A shape on the color field merges into the color legend.
        if let Some(field) = self.shape.as_deref().filter(|f| self.color() != Some(*f)) {
            let raw = if cfg.title.visible == Some(false) { String::new() } else { self.field_labels.get(field).cloned().unwrap_or_else(|| field.to_string()) };
            legends.push(EmptyLegend::title_only(LegendKind::Shape, raw, &self.background));
        }
        Ok(legends)
    }

    fn color_legend<'a>(
        &'a self,
        family: &str,
        cfg: &LegendConfig,
        series_palette: &[String],
        stack_up: bool,
    ) -> Result<Option<EmptyLegend<'a>>, Diagnostic> {
        let position = &cfg.position;
        let top_oriented = is_top_oriented(position);
        let wide = !self.wide_measures.is_empty();
        let color = self.color().filter(|_| !wide);
        // A wide fold split by a color dimension has no title and, over no rows, no entries:
        // production drops that legend (`drop_empty_legends`).
        if !cfg.visible || top_oriented || (color.is_none() && !wide && self.layers.is_empty()) || (wide && self.color.is_some()) {
            return Ok(None);
        }
        let layers = self.overlay_layers(family)?;
        let single_series_fill = || {
            self.style.single_series_fill.as_ref().ok_or("zero-row legend: style.single_series_fill absent")
        };
        let scatter = family == "scatter";
        let dot_plot = scatter && color.is_none() && !wide;
        let base: Vec<(String, String)> = if dot_plot {
            Vec::new()
        } else if wide {
            let mut series = self.wide_measures.clone();
            series.sort();
            let labels = display_text::wide_measure_labels_for(&self.wide_measures, &self.field_labels)?;
            series
                .iter()
                .map(|name| {
                    let fill = crate::common::series_color(name, &series, series_palette, None)?;
                    Ok((crate::common::wide_series_display(name, &labels), fill))
                })
                .collect::<Result<_, String>>()?
        } else if color.is_none() {
            let y = self.y.as_deref().ok_or("zero-row legend: payload y absent")?;
            vec![(crate::common::axis_title_text(y, self.y_label.as_deref(), &self.field_labels)?, single_series_fill()?.clone())]
        } else {
            Vec::new()
        };
        // A vertical stack lists its top segment first.
        let base = if wide && stack_up { base.into_iter().rev().collect() } else { base };
        let has_line_layer = layers.iter().any(|l| matches!(l, OverlayLayer::Line(_)));
        let has_bar_layer = layers.iter().any(|l| matches!(l, OverlayLayer::Bar(_)));
        let has_plain_line = layers.iter().any(|l| matches!(l, OverlayLayer::Line(l) if l.area_mark.is_none()));
        let mixed = !dot_plot && has_line_layer;
        let glyph = match family {
            "scatter" if mixed => BaseGlyph::Circle,
            "scatter" => BaseGlyph::FilledCircle,
            "bar" => BaseGlyph::Square,
            "area" if wide && stack_up => BaseGlyph::RingedCircle,
            "area" => BaseGlyph::Circle,
            _ if wide && layers.is_empty() => BaseGlyph::Circle,
            _ if color.is_some() && !has_line_layer && !has_bar_layer => BaseGlyph::Circle,
            _ if !has_plain_line => BaseGlyph::ShortStroke,
            _ => BaseGlyph::Stroke,
        };
        let base_title = match color {
            Some(field) if family == "area" => {
                if cfg.title.visible == Some(false) { String::new() } else { self.field_labels.get(field).cloned().unwrap_or_else(|| field.to_string()) }
            }
            Some(field) => legend_title_text(field, cfg, &self.field_labels)?,
            None => String::new(),
        };
        let title = merged_legend_title(base_title, color.is_some(), layers.iter().filter_map(OverlayLayer::color), cfg);
        let mut claims = Vec::new();
        let scale = if layers.is_empty() {
            None
        } else {
            let mut scale = LayerScale::new(&base, color.is_some());
            let empty: &[serde_json::Map<String, Value>] = &[];
            let pairs: Vec<_> = layers.iter().map(|l| (l, empty)).collect();
            claims = claim_layers(&mut scale, &pairs, &self.field_labels)?;
            Some(scale)
        };
        let scatter = scatter.then(|| {
            let labels = |claim: &crate::families::layers::LayerClaim| {
                std::iter::once(claim.label.clone()).chain(claim.values.iter().cloned()).collect::<Vec<_>>()
            };
            let bar_borders = layers
                .iter()
                .zip(&claims)
                .filter_map(|(layer, claim)| match layer {
                    OverlayLayer::Bar(bl) => Some((claim, bl.bar_mark.border.color.clone().unwrap_or_else(|| "none".into()))),
                    OverlayLayer::Line(_) | OverlayLayer::Scatter(_) => None,
                })
                .filter(|_| dot_plot)
                .flat_map(|(claim, border)| labels(claim).into_iter().map(move |l| (l, border.clone())))
                .collect();
            let unlisted = layers
                .iter()
                .zip(&claims)
                .filter(|(layer, _)| matches!(layer, OverlayLayer::Line(_)))
                .flat_map(|(_, claim)| labels(claim))
                .filter(|label| !base.iter().any(|(b, _)| b == label))
                .collect();
            ScatterLegend { mixed, bar_borders, unlisted }
        });
        let canvas_squares = family == "bar" && !has_line_layer && scale.is_some();
        let mixed_marks = scatter.is_some() && layers.iter().any(|l| matches!(l, OverlayLayer::Line(l) if l.area_mark.is_none()));
        let legend = EmptyLegend {
            kind: LegendKind::Color,
            title,
            base,
            scale,
            glyph,
            canvas: &self.background,
            scatter,
            canvas_squares,
            mixed_marks,
        };
        Ok((!legend.title.is_empty() || !legend.entries().is_empty()).then_some(legend))
    }
}
