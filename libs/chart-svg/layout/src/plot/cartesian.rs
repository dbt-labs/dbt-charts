//! The cartesian plot, family-agnostic: [`plan`] composes the chart's title, the plot-box
//! negotiation and both axes at a cell; [`draw`] paints the canvas, the title, the grids, the
//! family's marks (a painter the caller supplies) and the axes, in production's paint order.
//!
//! The y axis is a linear value axis. The x axis is a linear value axis or, on a time scale, the
//! [`TimeAxis`]: its ticks stand the gridlines (not the labels' anchors), a label of two rows (a
//! year under a month) is drawn as two texts a row apart, and a tilted or hidden label, an authored
//! tick count, label format or axis title are refused by name.
//!
//! The frame is family-agnostic: `plan`, `plan_at` and `draw` are replayed against the scatter
//! twins by `engine/tests/it/plot.rs`.

use bind::formats::locale::NumberLocale;
use bind::bound::{self, AxisStyle, Face, Family, Line, Mark, Orient, Panels, Scale, ScaleKind, Side as Edge, Ticks, TimeDomain};
use bind::plot::time::TimeUnitSetting;
use bind::table_dates::{strftime, Moment};
use bind::temporal::TimeUnit;
use d3rs::calendar::MS_PER_DAY;
use board_model::facet::FontWeightValue;
use board_model::style::axis::TextAlign;
use board_model::style::board::EmojiMode;
use board_model::style::style::Style;
use d3rs::format::parse_parts;
use diagnostic::{diag, refuse, Diagnostic};
use measure::shaped::{Font, FontBook};
use model_core::Engine;
use scene::{Attrs, Extent, Node, Role, Shape, Transform};

use crate::contract::{axis_description, axis_frame, axis_title_attrs, grid_group, linear_value_text, span_description};
use crate::geom::{Insets, Rect, Size};
use crate::plot::rules;
use crate::plot::band_axis::{BandAxis, BandGeometry, BandLabels, BandPlan, Overlap};
use crate::plot::label::{vega_baseline, vega_row, Align, Baseline};
use crate::plot::legend::SideLegend;
use crate::plot::negotiate::{body, negotiate};
use crate::plot::points::linear;
use crate::plot::time_axis::{day_extent, Clock, DayLabels, Placement as TimePlacement, TimeAxis, TimeLabels};
use crate::plot::time_cadence::Crowding;
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
    /// The pixels a time x range keeps off each plot edge (a line's `lines::Metrics::pad`); a
    /// time axis refuses an env that states none, a value axis ignores it.
    pub x_inset: Option<f64>,
}

/// What a family's painter draws: its marks in plot-local coordinates, and where in them production
/// paints the y axis (between the halo and the series line of a line); `None` paints the y axis
/// after the x axis, over the marks.
pub struct Painted {
    pub marks: Vec<Node>,
    pub y_axis_at: Option<usize>,
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

/// A time x axis as planned: its ticks (the x of each, where a gridline stands) and labels.
struct PlannedTime {
    grid: Option<Line>,
    /// How far the axis line hangs below the plot (`ticks.offset`).
    offset: f64,
    labels: Ink,
    placement: TimePlacement,
    ticks: Vec<f64>,
    caption: String,
}

enum XPlanned {
    Value(Planned),
    Time(PlannedTime),
}

/// The words a time axis names its periods and midnights with, in the board language.
struct TimeWords {
    quarters: [String; 4],
    clock: [String; 2],
}

/// A planned plot: the cell, the plot box in it, the title block and both axes.
pub struct PlotPlan {
    size: Size,
    canvas: String,
    heading: Option<(ChartTitle, Vec<Node>)>,
    pub plot: Rect,
    x: XPlanned,
    y: Planned,
    /// Whether the y label on the plot's bottom edge rises off its gridline.
    rises_bottom_label: bool,
    /// The side legend and its origin in the plot's coordinates.
    legend: Option<(SideLegend, (f64, f64))>,
}

impl PlotPlan {
    /// The warnings the card's width raised against the plot's title and subtitle.
    pub fn diagnostics(&self) -> &[Diagnostic] {
        self.heading.as_ref().map_or(&[], |(title, _)| &title.diagnostics)
    }
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

/// A discrete domain's description: its count and values, the middle elided past seven
/// (`domainCaption`'s `maxlen`).
fn discrete_description(count: usize, values: &[String]) -> String {
    const LISTED: usize = 7;
    let listed = match values.split_last() {
        Some((last, _)) if values.len() > LISTED => format!("{}, ending with {last}", values[..LISTED - 2].join(", ")),
        _ => values.join(", "),
    };
    format!("{count} value{}: {listed}", if count == 1 { "" } else { "s" })
}

/// Vega's description of a linear axis. A number format with an affix, a notation or a symbol is
/// refused: production captions those through the axis `format` it leaves unset
/// (`axis_caption.rs::slot_format`), which this does not model.
fn caption(channel: &str, title: Option<&str>, scale: &Scale, locale: &NumberLocale) -> Result<String, Diagnostic> {
    if let ScaleKind::Band(domain) = &scale.kind {
        return Ok(axis_description(channel, title, "discrete", &discrete_description(domain.keys().len(), domain.text())));
    }
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
    // A band axis's bottom labels have no edge for `inward` to resolve against, which a theme sets
    // for the bars' category axis in general.
    let band = matches!(scale.kind, ScaleKind::Band(_));
    let align_free = matches!(&labels.align, Engine::Rule(_)) || (band && matches!(&labels.align, Engine::Value(TextAlign::Inward)));
    if !matches!(&labels.angle, Engine::Rule(_)) || !align_free {
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

/// A time x axis over `domain` in a cell of `cell`. Its crowding is judged against the cell's width
/// by the theme's usable ratio, and a clipped edge period against the cell less the y axis'
/// width (`edge_width`), which production estimates before its fit settles.
fn time_axis<'a>(domain: &TimeDomain, style: &'a AxisStyle, parts: &'a Parts, cell: Size, edge_width: f64, env: &Env<'a>, words: &'a TimeWords) -> Result<TimeAxis<'a>, Diagnostic> {
    if parts.title.is_some() {
        return Err(Diagnostic::refused("a time x axis with a title is not supported"));
    }
    let time = style.time.as_ref().ok_or_else(|| diag::err_internal("bind hands a temporal x axis its crowding style"))?;
    let inset = env.x_inset.ok_or_else(|| diag::err_internal("a time x axis is planned with its edge pad stated"))?;
    let labels = TimeLabels {
        font: parts.labels.font(),
        limit: parts.limit,
        offset: parts.padding,
        min_gap: parts.min_gap,
        tick_unit: TimeUnitSetting::Absent,
        tick_step: None,
        label_unit: TimeUnitSetting::Absent,
        crowding: Crowding {
            skip: time.skip,
            tilt: time.tilt.as_deref(),
            usable_width: cell.width * time.usable_ratio,
            flush: parts.flush,
            font_size: parts.labels.size,
            bucket_aligned: false,
        },
        edge_width,
        inset,
        quarters: &words.quarters,
        cased: false,
        tick_count: false,
        date_format: false,
        clock: Clock { hours24: false, outer_width: cell.width, available_width: cell.width, day_labels: DayLabels::Off, words: &words.clock, points: true },
        spans_buckets: false,
        rail: false,
    };
    TimeAxis::new(domain, labels, env.book)
}

/// Vega's description of a time axis (`axisCaption`, `domainCaption`): a `utc` scale over the bucket
/// starts under a calendar grain, a `time` scale over the instants without one, its two ends printed
/// by the grain's default format (`temporal.rs::time_unit_specifier`) or, with no grain, by
/// `%A, %d %B %Y, %X`.
fn time_caption(domain: &TimeDomain) -> Result<String, Diagnostic> {
    let spec = match domain.unit {
        Some(TimeUnit::Year) => "%Y",
        Some(TimeUnit::YearQuarter) => "%Y Q%q",
        Some(TimeUnit::YearMonth) => "%b %Y",
        Some(TimeUnit::YearWeek) => "%Y W%U",
        Some(TimeUnit::YearMonthDate) => "%b %d, %Y",
        None => "%A, %d %B %Y, %X",
    }
    .replace("%a", "%A")
    .replace("%b", "%B");
    let (first, last) = day_extent(domain, false);
    let text = |day: f64| -> Result<String, Diagnostic> {
        let moment = Moment::from_epoch_millis((day * MS_PER_DAY).round() as i64).map_err(diag::err_internal)?;
        // d3-time-format pads `%Y` to four digits; strftime does not below 1000.
        let spec = spec.replace("%Y", &format!("{:04}", moment.year()));
        bind::time_zone::with_time_zone(&bind::time_zone::utc(), || strftime(moment, &spec)).map_err(diag::err_internal)
    };
    let kind = if domain.unit.is_some() { "utc" } else { "time" };
    Ok(axis_description("X", None, kind, &span_description(&text(first)?, &text(last)?)))
}

/// The x axis on a plot of `size`, hanging `offset` below the plot's bottom edge: its placement is
/// made on the plot grown by it and its bottom gutter counts it.
fn place_x(x: &ValueAxis, size: Size, offset: f64) -> Result<Placement, Diagnostic> {
    let mut placement = x.place(Size { width: size.width, height: size.height + offset })?;
    placement.gutter.bottom += offset;
    Ok(placement)
}

/// The x axis: a value axis over a linear scale, a band axis over a category scale, or a time axis.
enum XAxis<'a> {
    Value(ValueAxis<'a>),
    Band(BandPlan<'a>),
    Time(TimeAxis<'a>),
}

impl XAxis<'_> {
    /// How far the axis, hanging `offset` below the plot's bottom edge, reaches past a plot of `size`:
    /// its placement is made on the plot grown by the offset and its bottom gutter counts it.
    fn gutter(&self, size: Size, offset: f64) -> Result<Insets, Diagnostic> {
        match self {
            XAxis::Value(axis) => Ok(place_x(axis, size, offset)?.gutter),
            // The band axis hangs its labels `ticks.offset` further down from construction.
            XAxis::Band(band) => Ok(band.place(size)?.gutter),
            XAxis::Time(axis) => {
                let mut gutter = axis.place(size)?.gutter;
                gutter.bottom += offset;
                Ok(gutter)
            }
        }
    }
}

/// A band axis over `scale` for a plot of the bars' `style`: its labels flat in a card of `card`
/// px, or refused where they would tilt or collide.
fn band_axis<'a>(plot: &bound::Plane, scale: &Scale, parts: &'a Parts, grid: bool, card: f64, env: &'a Env) -> Result<BandPlan<'a>, Diagnostic> {
    let ScaleKind::Band(domain) = &scale.kind else {
        return Err(diag::err_internal("a band axis over a scale that is not a band"));
    };
    let Some(bars) = &plot.mark_style().bars else {
        return Err(diag::err_internal("a band axis on a plot with no bar mark"));
    };
    if parts.flush.is_some() {
        return Err(diag::err_internal("bind refuses a band axis that sets `labels.flush`: a band axis never flushes"));
    }
    if parts.edge != Edge::Bottom {
        return Err(Diagnostic::refused(refuse!("a band axis on the top edge is not supported")));
    }
    if grid {
        return Err(Diagnostic::refused(refuse!("a band axis with a grid is not supported")));
    }
    let labels = BandLabels {
        text: domain.text(),
        font: parts.labels.font(),
        overlap: Overlap::Flat,
        limit: parts.limit,
        tilt_width: card * bars.label_usable_ratio,
        offset: parts.offset + parts.padding,
    };
    let axis = BandAxis::new(&labels, env.book).map_err(diag::err_internal)?;
    if axis.angle() != 0.0 || axis.collision("", "").is_some() {
        return Err(Diagnostic::refused(refuse!("a band axis whose labels do not fit flat is not supported")));
    }
    let title = match (&parts.title, &parts.title_text) {
        (Some(ink), Some(text)) => Some(AxisTitle { text, font: ink.font(), padding: parts.title_padding }),
        _ => None,
    };
    let geometry = BandGeometry { padding_inner: bars.padding_inner, padding_outer: bars.padding_outer, align: 0.5, round: false };
    Ok(BandPlan { axis, geometry, book: env.book, title, font: parts.labels.font(), offset: parts.offset + parts.padding })
}

/// How far either axis's ink, and the side legend, reach past each edge of a plot of `size`.
fn reach(x: &XAxis, x_offset: f64, y: &ValueAxis, legend: Option<&SideLegend>, size: Size) -> Result<Insets, Diagnostic> {
    let (x, y) = (x.gutter(size, x_offset)?, y.place(size)?.gutter);
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
    // The zero rule is verified against production under bars alone.
    let bars = plot.layers().iter().all(|layer| layer.mark == Mark::Bars);
    if baselines.unity || baselines.x_threshold || (baselines.zero && !bars) {
        return Err(Diagnostic::refused("a plot with zero, unity or threshold rules is not supported"));
    }
    if plot.support().is_some() || plot.naming().rail.is_some() || plot.naming().asks_top_strip {
        return Err(Diagnostic::refused("a plot with an endpoint rail, a top legend strip or a support strip is not supported"));
    }
    if chart.subtitle.is_some() && chart.title.is_none() {
        return Err(Diagnostic::refused("a plot with a subtitle and no title is not supported"));
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
    let y_axis = axis(y_scale, &y_parts, false, env)?;
    let words = match &x_scale.kind {
        ScaleKind::Time(_) => Some(TimeWords {
            quarters: bind::time_locale::quarter_words().map_err(diag::err_internal)?,
            clock: bind::time_locale::clock_words().map_err(diag::err_internal)?,
        }),
        _ => None,
    };
    let x_axis = match (&x_scale.kind, &words) {
        (ScaleKind::Time(domain), Some(words)) => {
            side_of(x_parts.edge, true)?;
            let y_reach = y_axis.place(cell)?.gutter;
            let y_width = if y_parts.edge == Edge::Left { y_reach.left } else { y_reach.right };
            XAxis::Time(time_axis(domain, &styles.x, &x_parts, cell, (cell.width - y_width).max(1.0), env, words)?)
        }
        (ScaleKind::Band(_), _) => XAxis::Band(band_axis(plot, x_scale, &x_parts, styles.x.grid.is_some(), cell.width, env)?),
        _ => XAxis::Value(axis(x_scale, &x_parts, true, env)?),
    };

    let legend = SideLegend::of(plot, &plot.naming().authored, env.emoji, env.book, cell.height - padding.top - padding.bottom)?;
    let block = heading_block(chart, heading, cell.width, (padding.left, padding.top), padding.right, env.book, env.emoji)?;
    let area = body(
        Rect { x: 0.0, y: 0.0, width: cell.width, height: cell.height },
        padding,
        block.as_ref().map(|(title, _)| &title.band),
        Insets::ZERO,
    );
    let rule_overhang = match (&styles.y[0].threshold, baselines.zero) {
        (Some(line), true) => line.width,
        _ => 0.0,
    };
    let plot_box = match pinned {
        Some(plot_box) => plot_box,
        None => negotiate(area, |size| {
            let axes = reach(&x_axis, x_parts.offset, &y_axis, legend.as_ref(), size)?;
            // The zero rule's full stroke width overhangs every edge (`bar/vertical.rs`'s
            // rule bounds).
            Ok(Insets { top: axes.top.max(rule_overhang), right: axes.right.max(rule_overhang), bottom: axes.bottom.max(rule_overhang), left: axes.left.max(rule_overhang) })
        })?
        .plot,
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
    let x = match &x_axis {
        XAxis::Value(axis) => XPlanned::Value(planned(place_x(axis, at, x_parts.offset)?, &x_parts, x_scale, &styles.x, "X")?),
        XAxis::Band(band) => XPlanned::Value(planned(band.place(at)?, &x_parts, x_scale, &styles.x, "X")?),
        XAxis::Time(axis) => {
            let ScaleKind::Time(domain) = &x_scale.kind else {
                return Err(diag::err_internal("a time axis over a scale that is not a time scale"));
            };
            let placement = axis.place(at)?;
            if placement.interval {
                return Err(Diagnostic::refused(refuse!("a time axis whose labels stand between period boundaries draws tick marks, which is not supported")));
            }
            let ticks = placement.ticks.iter().map(|day| axis.x_of(*day, at.width)).collect();
            XPlanned::Time(PlannedTime { grid: styles.x.grid.clone(), offset: x_parts.offset, labels: x_parts.labels.clone(), placement, ticks, caption: time_caption(domain)? })
        }
    };
    let y = planned(y_axis.place(at)?, &y_parts, y_scale, &styles.y[0], "Y")?;
    // Production rises a y label whose tick is on the plot's bottom edge off its gridline when the
    // x axis hangs below (`YEdges::baseline`); the draw does so under bars, where a twin pins it,
    // and every other family refuses it, whether or not the x labels draw (which only over-refuses).
    if !bars && y.placement.rungs.iter().any(|rung| rung.visible && (rung.y - at.height).abs() <= 0.5) {
        return Err(Diagnostic::refused("a y axis label on the plot's bottom edge is not supported"));
    }
    let legend = legend.map(|legend| {
        let origin = legend.origin(at, y.placement.gutter);
        (legend, origin)
    });
    Ok(PlotPlan { size: cell, canvas: opaque_canvas(frame)?, heading: block, plot: plot_box, x, y, legend, rises_bottom_label: bars })
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
/// fitted title and its nodes at the card's padded top left `(left, top)`, `None` when the text is
/// blank. `width` is the card's outer width, which picks the title's face, and the line may take what
/// `right` padding and `left` leave of it.
pub(super) fn heading_block(chart: &bound::Chart, heading: Option<&Heading>, width: f64, (left, top): (f64, f64), right: f64, book: &FontBook, emoji: &EmojiMode) -> Result<Option<(ChartTitle, Vec<Node>)>, Diagnostic> {
    if chart.title.is_some() != heading.is_some() {
        return Err(diag::err_internal("a chart's heading is resolved exactly when the chart has a title"));
    }
    let Some(h) = heading else { return Ok(None) };
    let text = chart.title.as_deref().ok_or_else(|| diag::err_internal("a heading is resolved for a titled chart"))?;
    let Some(title) = chart_title(h.chart_id, text, chart.subtitle.as_deref(), h.style, width, width - left - right, book)? else {
        return Ok(None);
    };
    let nodes = title.nodes((left, top), &h.style.charts.title, emoji);
    Ok(Some((title, nodes)))
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

/// A grid of `line`s crossing the plot `extent` px, one at each of `positions` along the other axis.
fn grid(line: &Line, vertical: bool, extent: f64, positions: impl Iterator<Item = f64>) -> Result<Node, Diagnostic> {
    if line.dash.is_some() {
        return Err(Diagnostic::refused("a dashed grid is not supported"));
    }
    let lines = positions
        .map(|position| {
            let attrs = Attrs::new().set("stroke", line.color.as_str()).num("stroke-width", line.width).num("opacity", line.opacity);
            let at = position.round() + CRISP;
            if vertical {
                Node::line(Role::Grid, (at, CRISP), (at, extent + CRISP), attrs)
            } else {
                Node::line(Role::Grid, (CRISP, at), (extent + CRISP, at), attrs)
            }
        })
        .collect();
    Ok(grid_group(lines))
}

/// A time axis' labels, a text per row (the year under a month stands a row below). `height` is the
/// plot's, which the labels hang below.
fn time_axis_group(planned: &PlannedTime, height: f64) -> Result<Node, Diagnostic> {
    let size = planned.labels.size;
    let mut items = Vec::new();
    for label in &planned.placement.labels {
        if !label.visible {
            return Err(Diagnostic::refused("a time axis label hidden by the overlap rule is not supported"));
        }
        if label.angle != 0.0 {
            return Err(Diagnostic::refused("a tilted time axis label is not supported"));
        }
        let first = vega_baseline(height + planned.offset + label.y, label.baseline, size) + CRISP;
        for (row, text) in label.text.split('\n').enumerate() {
            let at = (label.x + CRISP, first + row as f64 * vega_row(size));
            items.push(Node::text(Role::Label, at, planned.labels.attrs(Some(anchor(label.align))), text));
        }
    }
    Ok(axis_frame("x-axis", &planned.caption, (CRISP, CRISP), items))
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

/// `plot_height` is the plot's height; `rises_bottom_label` is a y axis under an x axis whose labels
/// draw: production rises a y label whose tick sits on the plot's bottom edge off its gridline
/// (`YEdges::baseline`, a verdict of the chart's structure alone).
fn axis_group(planned: &Planned, key: &str, authored: &str, plot_height: f64, rises_bottom_label: bool) -> Result<Node, Diagnostic> {
    let mut items = Vec::new();
    if let Some(ticks) = &planned.ticks {
        items.extend(tick_marks(planned, ticks, plot_height));
    }
    for rung in &planned.placement.rungs {
        if !rung.visible {
            return Err(Diagnostic::refused("a value axis label hidden by the overlap rule is not supported"));
        }
        let baseline = if rises_bottom_label && (rung.y - plot_height).abs() <= 0.5 { Baseline::Bottom } else { rung.baseline };
        let at = (rung.x + CRISP, vega_baseline(rung.y, baseline, planned.labels.font().size) + CRISP);
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
    paint: impl Fn(&bound::Plane, Size) -> Result<Painted, Diagnostic>,
) -> Result<Vec<Node>, Diagnostic> {
    let Size { width, height } = plan.size;
    let mut children = vec![Node::rect(Role::Chrome, (0.0, 0.0, width, height), Attrs::new().set("fill", plan.canvas.as_str()))];
    if let Some((_, nodes)) = &plan.heading {
        children.extend(nodes.iter().cloned());
    }
    let at = plan.plot.size();
    let mut inner = Vec::new();
    let rung_positions = |planned: &Planned, vertical: bool| -> Vec<f64> { planned.placement.rungs.iter().map(|rung| if vertical { rung.x } else { rung.y }).collect() };
    let x_grid = match &plan.x {
        XPlanned::Value(planned) => planned.grid.as_ref().map(|line| (line, rung_positions(planned, true))),
        XPlanned::Time(planned) => planned.grid.as_ref().map(|line| (line, planned.ticks.clone())),
    };
    if let Some((line, positions)) = x_grid {
        inner.push(grid(line, true, at.height, positions.into_iter())?);
    }
    if let Some(line) = &plan.y.grid {
        inner.push(grid(line, false, at.width, rung_positions(&plan.y, false).into_iter())?);
    }
    let Painted { mut marks, y_axis_at } = paint(plot, at)?;
    if plot.baselines().zero {
        marks.push(rules::zero_rule(plot, at)?);
    }
    let y_axis = axis_group(&plan.y, "y-axis", "y_label", at.height, plan.rises_bottom_label)?;
    let x_axis = match &plan.x {
        XPlanned::Value(planned) => axis_group(planned, "x-axis", "x_label", at.height, false)?,
        XPlanned::Time(planned) => time_axis_group(planned, at.height)?,
    };
    match y_axis_at {
        Some(index) if index <= marks.len() => {
            marks.insert(index, y_axis);
            marks.push(x_axis);
        }
        Some(index) => return Err(diag::err_internal(format!("a painter puts the y axis at mark {index} of {}", marks.len()))),
        None => marks.extend([x_axis, y_axis]),
    }
    inner.extend(marks);
    inner.extend(plan.legend.iter().map(|(legend, origin)| legend.nodes(*origin)));
    children.push(Node::group(Role::Chrome, Attrs::new(), inner).translate(plan.plot.x, plan.plot.y));
    let root = Node::new(Role::Chrome, Attrs::new().set("class", "marks"), Shape::Svg { extent: Extent { width, height }, children });
    Ok(vec![root])
}
