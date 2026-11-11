//! The cartesian plot, family-agnostic: [`plan`] composes the chart's title, the plot-box
//! negotiation and both value axes at a cell; [`draw`] paints the canvas, the title, the grids, the
//! family's marks (a painter the caller supplies) and the axes, in production's paint order.
//!
//! The frame is family-agnostic: `plan`, `plan_at` and `draw` are replayed against the scatter
//! twins by `engine/tests/it/plot.rs`.

use bind::formats::locale::NumberLocale;
use bind::bound::{Ticks, self, AxisStyle, Family, Face, Line, Orient, Panels, Scale, Side as Edge};
use board_model::facet::FontWeightValue;
use board_model::style::board::EmojiMode;
use board_model::style::style::Style;
use d3rs::format::parse_parts;
use diagnostic::{diag, Diagnostic};
use measure::shaped::{Font, FontBook};
use model_core::Engine;
use scene::{Attrs, Extent, Node, Role, Shape, Transform};

use crate::contract::{axis_description, axis_frame, axis_title_attrs, grid_group, linear_value_text, span_description};
use crate::geom::{Insets, Rect, Size};
use crate::plot::label::{vega_baseline, Align};
use crate::plot::legend::SideLegend;
use crate::plot::negotiate::{body, negotiate};
use crate::plot::points::linear;
use crate::plot::value_axis::{direction, AxisTitle, GutterRule, Placement, Side, ValueAxis, ValueLabels};
use crate::title::{chart_title, ChartTitle};

/// Vega's default `labelLimit`, which an `auto` `max_width` stands for.
const LABEL_LIMIT: f64 = 180.0;

/// What a plan measures and prints with.
pub struct Env<'a> {
    pub book: &'a FontBook,
    pub locale: &'a NumberLocale,
    pub emoji: &'a EmojiMode,
    /// The family's gutter law (`GutterRule`).
    pub rule: GutterRule,
    /// The board's `style.frame.card_padding`, which the board layer adds to the chart's own
    /// padding on every side.
    pub card_padding: f64,
}

/// What a chart's title is resolved from: the board scope's total style, whose chart title slot picks
/// the width-tier face (`chart_title`).
pub struct Heading<'a> {
    pub chart_id: &'a str,
    pub style: &'a Style,
}

/// A text face as painted: the family stack with the board's emoji family, size, weight and ink.
#[derive(Clone)]
pub(crate) struct Ink {
    family: String,
    pub(crate) size: f64,
    weight: f32,
    color: String,
}

impl Ink {
    pub(crate) fn of(face: &Face, emoji: &EmojiMode) -> Result<Self, Diagnostic> {
        let weight = match &face.weight {
            FontWeightValue::Number(n) => *n as f32,
            FontWeightValue::Name(name) => name.parse().map_err(|_| {
                Diagnostic::refused("an axis font weight given by name is not supported")
            })?,
        };
        Ok(Self {
            family: bind::font_stack::with_emoji(&face.family, emoji),
            size: face.size,
            weight,
            color: face.color.clone(),
        })
    }

    pub(crate) fn font(&self) -> Font<'_> {
        Font { family: &self.family, size: self.size, weight: self.weight, italic: false }
    }

    pub(crate) fn attrs(&self, anchor: Option<&str>) -> Attrs {
        let attrs = match anchor {
            Some(anchor) => Attrs::new().set("text-anchor", anchor),
            None => Attrs::new(),
        };
        attrs
            .vega("font-family", self.family.as_str())
            .num("font-size", self.size)
            .set("font-style", "normal")
            .num("font-weight", f64::from(self.weight))
            .set("fill", self.color.as_str())
    }
}

/// One planned axis: what its style draws and where its labels and title sit.
struct Planned {
    grid: Option<Line>,
    /// The tick marks an x axis draws.
    ticks: Option<Ticks>,
    /// The gap between the labels' anchor and the tick's end.
    label_padding: f64,
    labels: Ink,
    title: Option<Ink>,
    placement: Placement,
    caption: String,
}

/// A planned plot: the cell, the plot box in it, the title block and both axes.
pub struct PlotPlan {
    size: Size,
    canvas: String,
    padding: Insets,
    heading: Option<(ChartTitle, Ink)>,
    pub plot: Rect,
    x: Planned,
    y: Planned,
    /// The side legend and its origin in the plot's coordinates.
    legend: Option<(SideLegend, (f64, f64))>,
}

fn side_of(edge: Edge, horizontal: bool) -> Result<Side, Diagnostic> {
    match (edge, horizontal) {
        (Edge::Bottom, true) => Ok(Side::Bottom),
        (Edge::Top, true) => Err(Diagnostic::refused("an x axis on the top edge is not supported")),
        (Edge::Left, false) => Ok(Side::Left),
        (Edge::Right, false) => Ok(Side::Right),
        _ => Err(diag::err_internal(format!("a value axis on the {edge:?} edge of the wrong orientation"))),
    }
}

/// Vega's description of a linear axis. A number format with an affix, a notation or a symbol is
/// refused: production captions those through the axis `format` it leaves unset
/// (`axis_caption.rs::slot_format`), which this does not model.
fn caption(channel: &str, title: Option<&str>, scale: &Scale, locale: &NumberLocale) -> Result<String, Diagnostic> {
    let axis = linear(scale, channel)?;
    let format_spec = &axis.format;
    let parts = parse_parts(&format_spec.spec).map_err(diag::err_internal)?;
    if !format_spec.prefix.is_empty() || !format_spec.suffix.is_empty() || format_spec.notation.is_some() || parts.symbol {
        return Err(Diagnostic::refused("an axis description for a number format with an affix, a notation or a symbol is not supported"));
    }
    let (start, stop) = direction(&axis.domain);
    let text = |value: f64| linear_value_text(&locale.locale, Some(&format_spec.spec), (start, stop), value).map_err(diag::err_internal);
    Ok(axis_description(channel, title, "linear", &span_description(&text(start)?, &text(stop)?)))
}

/// An axis laid over `scale` by its walked `style`, with the faces it paints in and the title text.
struct Parts {
    edge: Edge,
    labels: Ink,
    title: Option<Ink>,
    title_text: Option<String>,
    padding: f64,
    title_padding: f64,
    min_gap: f64,
    flush: Option<f64>,
    limit: f64,
    offset: f64,
    ticks: Option<Ticks>,
}

fn parts(style: &AxisStyle, scale: &Scale, env: &Env) -> Result<Parts, Diagnostic> {
    if style.domain.is_some() {
        return Err(Diagnostic::refused("a value axis with a domain line is not supported"));
    }
    if style.ticks.is_some() && style.side != Edge::Bottom {
        return Err(Diagnostic::refused("a value axis with tick marks off the bottom edge is not supported"));
    }
    let Some(labels) = &style.labels else {
        return Err(Diagnostic::refused("a value axis with hidden labels is not supported"));
    };
    let limit = match labels.max_width {
        Engine::Value(width) => width,
        Engine::Rule(_) => LABEL_LIMIT,
    };
    if !matches!((&labels.angle, &labels.align), (Engine::Rule(_), Engine::Rule(_))) {
        return Err(Diagnostic::refused("a value axis label with an authored angle or alignment is not supported"));
    }
    let title = style
        .title
        .as_ref()
        .map(|title| {
            if !matches!((&title.angle, &title.align), (Engine::Rule(_), Engine::Rule(_))) {
                return Err(Diagnostic::refused("a value axis title with an authored angle or alignment is not supported"));
            }
            Ok((Ink::of(&title.face, env.emoji)?, title.padding))
        })
        .transpose()?;
    if title.is_some() != scale.title.is_some() {
        return Err(diag::err_internal("bind hands a scale its title text exactly where its axis style draws a title"));
    }
    let (title, title_padding) = title.map_or((None, 0.0), |(ink, padding)| (Some(ink), padding));
    Ok(Parts {
        edge: style.side,
        labels: Ink::of(&labels.face, env.emoji)?,
        title,
        title_text: scale.title.clone(),
        padding: labels.padding,
        title_padding,
        min_gap: labels.min_gap,
        flush: labels.flush,
        limit,
        offset: style.offset,
        ticks: style.ticks.clone(),
    })
}

fn axis<'a>(scale: &'a Scale, parts: &'a Parts, horizontal: bool, env: &'a Env) -> Result<ValueAxis<'a>, Diagnostic> {
    let value = linear(scale, if horizontal { "x" } else { "y" })?;
    let title = match (&parts.title, &parts.title_text) {
        (Some(ink), Some(text)) => Some(AxisTitle { text, font: ink.font(), padding: parts.title_padding }),
        _ => None,
    };
    let labels = ValueLabels {
        font: parts.labels.font(),
        format: &value.format,
        locale: env.locale,
        side: side_of(parts.edge, horizontal)?,
        min_gap: parts.min_gap,
        flush: parts.flush,
        limit: parts.limit,
        tick: parts.ticks.as_ref().map_or(0.0, |ticks| ticks.length),
        padding: parts.padding,
        rule: env.rule,
        estimate_extent: value.domain.extent,
        mirror: None,
        title,
    };
    ValueAxis::new(&value.domain, labels, env.book)
}

/// The x axis on a plot of `size`, hanging `offset` below the plot's bottom edge: its placement is
/// made on the plot grown by it and its bottom gutter counts it.
fn place_x(x: &ValueAxis, size: Size, offset: f64) -> Result<Placement, Diagnostic> {
    let mut placement = x.place(Size { width: size.width, height: size.height + offset })?;
    placement.gutter.bottom += offset;
    Ok(placement)
}

/// How far either axis's ink, and the side legend, reach past each edge of a plot of `size`.
fn reach(x: &ValueAxis, x_offset: f64, y: &ValueAxis, legend: Option<&SideLegend>, size: Size) -> Result<Insets, Diagnostic> {
    let (x, y) = (place_x(x, size, x_offset)?.gutter, y.place(size)?.gutter);
    let axes = Insets {
        top: x.top.max(y.top),
        right: x.right.max(y.right),
        bottom: x.bottom.max(y.bottom),
        left: x.left.max(y.left),
    };
    Ok(legend.map_or(axes, |legend| legend.widen(axes, size, y)))
}

/// The plot of `chart` in a cell of `cell` px: the title above, then the plot box the two value
/// axes leave. `heading` is the chart's title resolved, present exactly when the chart has one.
/// What the walked styles draw that is not ported here is refused by name; those refusals are
/// plain `Diagnostic::refused`, allowlisted as invariants until a caller reaches them.
pub fn plan(chart: &bound::Chart, heading: Option<&Heading>, cell: Size, env: &Env) -> Result<PlotPlan, Diagnostic> {
    lay_out(chart, heading, cell, None, env)
}

/// [`plan`] with the plot box given instead of negotiated, in the cell's coordinates: for a caller
/// that knows the box it must draw (a replay of a recorded render), the title and axes still laid
/// as `plan` lays them.
pub fn plan_at(chart: &bound::Chart, heading: Option<&Heading>, cell: Size, plot: Rect, env: &Env) -> Result<PlotPlan, Diagnostic> {
    lay_out(chart, heading, cell, Some(plot), env)
}

fn lay_out(chart: &bound::Chart, heading: Option<&Heading>, cell: Size, pinned: Option<Rect>, env: &Env) -> Result<PlotPlan, Diagnostic> {
    let (Ok(Family::Cartesian(bound::Cartesian::Plane(plot))), Some(frame)) = (&chart.family, &chart.frame) else {
        return Err(diag::err_internal("a plot plan reached a chart that is not a plot with a frame"));
    };
    let plot: &bound::Plane = plot;
    if plot.orient() != Orient::Vertical || plot.y().len() != 1 {
        return Err(diag::err_internal("a plot of horizontal marks or several measures is not laid out here"));
    }
    if plot.panels() != &Panels::Single {
        return Err(Diagnostic::refused("a plot of facet panels is not supported"));
    }
    let baselines = plot.baselines();
    if baselines.zero || baselines.unity || baselines.x_threshold {
        return Err(Diagnostic::refused("a plot with zero, unity or threshold rules is not supported"));
    }
    if plot.support().is_some() || plot.naming().rail.is_some() || plot.naming().asks_top_strip {
        return Err(Diagnostic::refused("a plot with an endpoint rail, a top legend strip or a support strip is not supported"));
    }
    if chart.subtitle.is_some() {
        return Err(Diagnostic::refused("a plot with a subtitle is not supported"));
    }
    let padding = Insets {
        top: frame.padding.top + env.card_padding,
        right: frame.padding.right + env.card_padding,
        bottom: frame.padding.bottom + env.card_padding,
        left: frame.padding.left + env.card_padding,
    };
    let styles = plot.axis_styles();
    let (x_scale, y_scale) = (plot.x(), &plot.y()[0]);
    let (x_parts, y_parts) = (parts(&styles.x, x_scale, env)?, parts(&styles.y[0], y_scale, env)?);
    if y_parts.offset != 0.0 {
        return Err(Diagnostic::refused("a y axis with a `ticks.offset` is not supported"));
    }
    let (x_axis, y_axis) = (axis(x_scale, &x_parts, true, env)?, axis(y_scale, &y_parts, false, env)?);

    let legend = SideLegend::of(plot, &plot.naming().authored, env.emoji, env.book, cell.height - padding.top - padding.bottom)?;
    let block = heading_block(chart, heading, cell.width, cell.width - padding.left - padding.right, env.book, env.emoji)?;
    let area = body(
        Rect { x: 0.0, y: 0.0, width: cell.width, height: cell.height },
        padding,
        block.as_ref().map(|(title, _)| &title.metrics),
        Insets::ZERO,
    );
    let plot_box = match pinned {
        Some(plot_box) => plot_box,
        None => negotiate(area, |size| reach(&x_axis, x_parts.offset, &y_axis, legend.as_ref(), size))?.plot,
    };
    let at = plot_box.size();
    let planned = |placement: Placement, parts: &Parts, scale: &Scale, style: &AxisStyle, channel: &str| -> Result<Planned, Diagnostic> {
        Ok(Planned {
            grid: style.grid.clone(),
            ticks: parts.ticks.clone(),
            label_padding: parts.padding,
            caption: caption(channel, parts.title_text.as_deref(), scale, env.locale)?,
            placement,
            labels: parts.labels.clone(),
            title: parts.title.clone(),
        })
    };
    let x = planned(place_x(&x_axis, at, x_parts.offset)?, &x_parts, x_scale, &styles.x, "X")?;
    let y = planned(y_axis.place(at)?, &y_parts, y_scale, &styles.y[0], "Y")?;
    // Production rises a y label whose tick is on the plot's bottom edge off its gridline when the
    // x axis hangs below (`YEdges::baseline`); the walk draws every label at the middle. Refused
    // whether or not the x labels draw, which only over-refuses.
    if y.placement.rungs.iter().any(|rung| rung.visible && (rung.y - at.height).abs() <= 0.5) {
        return Err(Diagnostic::refused("a y axis label on the plot's bottom edge is not supported"));
    }
    let legend = legend.map(|legend| {
        let origin = legend.origin(at, y.placement.gutter);
        (legend, origin)
    });
    Ok(PlotPlan { size: cell, canvas: opaque_canvas(frame)?, padding, heading: block, plot: plot_box, x, y, legend })
}

/// The chart's canvas as the hex ink under its drawing, which reaches layout opaque.
pub(super) fn opaque_canvas(frame: &bound::Frame) -> Result<String, Diagnostic> {
    let [r, g, b, a] = [frame.canvas.0, frame.canvas.1, frame.canvas.2, frame.canvas.3];
    if a != 1.0 {
        return Err(diag::err_internal("a chart canvas reaches layout opaque"));
    }
    Ok(dbt_charts_color::rgb01_to_hex(r, g, b))
}

/// The chart's title resolved against its heading (present exactly when the chart has a title): the
/// fitted block and the ink it paints in, `None` when the text is blank. `width` is the card's outer
/// width, which picks the title's face, and `avail` the width the line may take.
pub(super) fn heading_block(chart: &bound::Chart, heading: Option<&Heading>, width: f64, avail: f64, book: &FontBook, emoji: &EmojiMode) -> Result<Option<(ChartTitle, Ink)>, Diagnostic> {
    if chart.title.is_some() != heading.is_some() {
        return Err(diag::err_internal("a chart's heading is resolved exactly when the chart has a title"));
    }
    let Some(h) = heading else { return Ok(None) };
    let text = chart.title.as_deref().ok_or_else(|| diag::err_internal("a heading is resolved for a titled chart"))?;
    let Some(title) = chart_title(h.chart_id, text, h.style, width, avail, book)? else {
        return Ok(None);
    };
    let face = Face { family: title.family.clone(), size: title.size, weight: title.weight.clone(), color: h.style.charts.title.font.color.as_str().to_string() };
    let ink = Ink::of(&face, emoji)?;
    Ok(Some((title, ink)))
}

/// The title's text on its baseline at the plot's top left.
pub(super) fn title_node(padding: &Insets, title: &ChartTitle, ink: &Ink) -> Node {
    let attrs = ink.attrs(None).set("data-authored-kind", "title");
    Node::text(Role::Title, (padding.left, padding.top + title.metrics.ascent), attrs, title.text.as_str())
}

fn anchor(align: Align) -> &'static str {
    match align {
        Align::Left => "start",
        Align::Center => "middle",
        Align::Right => "end",
    }
}

/// Production's crisp half pixel, on which every axis item sits.
const CRISP: f64 = 0.5;

fn grid(planned: &Planned, line: &Line, vertical: bool, extent: f64) -> Result<Node, Diagnostic> {
    if line.dash.is_some() {
        return Err(Diagnostic::refused("a dashed grid is not supported"));
    }
    let lines = planned
        .placement
        .rungs
        .iter()
        .map(|rung| {
            let attrs = Attrs::new().set("stroke", line.color.as_str()).num("stroke-width", line.width).num("opacity", line.opacity);
            let at = (if vertical { rung.x } else { rung.y }).round() + CRISP;
            if vertical {
                Node::line(Role::Grid, (at, CRISP), (at, extent + CRISP), attrs)
            } else {
                Node::line(Role::Grid, (CRISP, at), (extent + CRISP, at), attrs)
            }
        })
        .collect();
    Ok(grid_group(lines))
}

/// The x axis's tick marks: one stub at each rung from the plot's bottom edge to the tick's length below
/// the axis group's own offset, where the labels' anchor sits `padding` further (`x_tick_marks`).
fn tick_marks(planned: &Planned, ticks: &Ticks, plot_height: f64) -> Vec<Node> {
    let end = planned.placement.rungs.first().map_or(plot_height, |rung| rung.y - planned.label_padding + CRISP);
    planned
        .placement
        .rungs
        .iter()
        .map(|rung| {
            let attrs = Attrs::new().set("stroke", ticks.line.color.as_str()).num("stroke-width", ticks.line.width);
            let at = rung.x.round() + CRISP;
            Node::line(Role::Axis, (at, plot_height), (at, end), attrs)
        })
        .collect()
}

fn axis_group(planned: &Planned, key: &str, authored: &str, plot_height: f64) -> Result<Node, Diagnostic> {
    let mut items = Vec::new();
    if let Some(ticks) = &planned.ticks {
        items.extend(tick_marks(planned, ticks, plot_height));
    }
    for rung in &planned.placement.rungs {
        if !rung.visible {
            return Err(Diagnostic::refused("a value axis label hidden by the overlap rule is not supported"));
        }
        let at = (rung.x + CRISP, vega_baseline(rung.y, rung.baseline, planned.labels.font().size) + CRISP);
        items.push(Node::text(Role::Label, at, planned.labels.attrs(Some(anchor(rung.align))), rung.text.as_str()));
    }
    if let (Some(title), Some(ink)) = (&planned.placement.title, &planned.title) {
        // A turned title is anchored on its own x and y, with no baseline shift.
        let y = if title.turn.is_some() { title.y } else { vega_baseline(title.y, title.baseline, ink.size) };
        let at = (title.x + CRISP, y + CRISP);
        let text = Node::text(Role::Axis, at, ink.attrs(Some("middle")), title.text.as_str());
        let text = match title.turn {
            Some(deg) => text.with_transform(Transform::Rotate { deg, about: Some(at) }),
            None => text,
        };
        items.push(Node::group(Role::Axis, axis_title_attrs(Some(authored)), vec![text]));
    }
    Ok(axis_frame(key, &planned.caption, (CRISP, CRISP), items))
}

/// The plot as nodes: the chart's root `<svg>` with its canvas, title, grids, `paint`'s marks
/// (plot-local, for the plot's size) and axes. `plot` is the one `plan` was made from.
pub fn draw(
    plot: &bound::Plane,
    plan: &PlotPlan,
    paint: impl Fn(&bound::Plane, Size) -> Result<Vec<Node>, Diagnostic>,
) -> Result<Vec<Node>, Diagnostic> {
    let Size { width, height } = plan.size;
    let mut children = vec![Node::rect(Role::Chrome, (0.0, 0.0, width, height), Attrs::new().set("fill", plan.canvas.as_str()))];
    if let Some((title, ink)) = &plan.heading {
        children.push(title_node(&plan.padding, title, ink));
    }
    let at = plan.plot.size();
    let mut inner = Vec::new();
    for (planned, vertical, extent) in [(&plan.x, true, at.height), (&plan.y, false, at.width)] {
        if let Some(line) = &planned.grid {
            inner.push(grid(planned, line, vertical, extent)?);
        }
    }
    inner.extend(paint(plot, at)?);
    inner.push(axis_group(&plan.x, "x-axis", "x_label", at.height)?);
    inner.push(axis_group(&plan.y, "y-axis", "y_label", at.height)?);
    inner.extend(plan.legend.iter().map(|(legend, origin)| legend.nodes(*origin)));
    children.push(Node::group(Role::Chrome, Attrs::new(), inner).translate(plan.plot.x, plan.plot.y));
    let root = Node::new(Role::Chrome, Attrs::new().set("class", "marks"), Shape::Svg { extent: Extent { width, height }, children });
    Ok(vec![root])
}
