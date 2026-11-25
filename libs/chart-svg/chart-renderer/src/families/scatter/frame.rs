//! A scatter with `style.frame: range`: each axis is a range frame (a line from the least to the
//! greatest value, a tick and a label at each end), and the y title sits above the plot.

use measure::{FontBook, Measure};
use scene::{Attrs, Node, PathData, Role};
use serde_json::Value;

use super::bind::Bound;
use super::{quantitative_color, ScatterResolved, Shape};
use crate::common::{format_mark_label, AxisStyle, Font, FormatSlot};
use crate::families::layers::OverlayLayer;
use crate::families::legend::LegendAlign;
use crate::families::scatter_shape::SeriesSymbol;
use crate::frame::{ChartFrame, FrameTitle};
use crate::layout::{Layout, Rect};
use crate::vega_fit::Bounds;
use crate::Payload;
use ::bind::rows::{row_num, row_str};
use diagnostic::Diagnostic;

const FRAME_TICK: f64 = 4.0;
const LABEL_GAP: f64 = 3.0;
const X_LABEL_DROP: f64 = 12.0;
const X_TITLE_DROP: f64 = 26.0;
/// How far the y title (or, with none, the key) sits over the frame's visible top: its baseline to the top tick.
const TITLE_OVER_FRAME: f64 = 12.0;
/// The distance from the key's baseline to the top of the y title's capitals.
const KEY_OVER_TITLE_CAPS: f64 = 20.0;
const KEY_SYMBOL_X: f64 = 5.0;
const KEY_NAME_X: f64 = 14.0;
const KEY_ENTRY_GAP: f64 = 16.0;
const MIDDLE_DROP: f64 = 0.35;

pub(super) fn refuse_shape(r: &ScatterResolved, overlays: &[OverlayLayer], shape: &Shape) -> Result<(), String> {
    let point = &r.style.point_mark;
    if r.style.frame == super::Frame::Full {
        return match (&r.style.series_symbols, point.halo) {
            (None, None) => Ok(()),
            _ => Err(diagnostic::refuse!("scatter: `series_symbols` or `marks.point.halo` without `frame: range` is not supported")),
        };
    }
    let axes = [("axis_x", &r.style.axis_x, "bottom"), ("axis_y", &r.style.axis_y, "left")];
    let undrawn_axis = axes.iter().find_map(|&(name, ax, side)| undrawn_axis_key(ax, side).map(|key| format!("`{name}.{key}`")));
    let unframed = [
        (!overlays.is_empty(), "`layers:`"),
        (r.multiples.is_some(), "`multiples:`"),
        (shape.x_dates || shape.x_nominal, "a date or category x"),
        (shape.y_nominal, "a category y"),
        (!r.wide_measures.is_empty(), "several `y:` measures"),
        (r.size.is_some(), "`size:`"),
        (r.shape.is_some(), "`shape:`"),
        (quantitative_color(r), "a numeric `color:`"),
        (!r.annotations.is_empty(), "`annotations:`"),
        (r.style.conditional.is_some(), "`style.conditional`"),
        (point.labels.as_ref().is_some_and(|l| l.visible), "point value labels"),
        (point.shape.is_some() || point.filled == Some(false), "an authored point shape or an unfilled point"),
        (point.opacity.is_some_and(|o| o != 1.0), "`marks.point.opacity`"),
        (r.style.axis_y.mirror_requested("scatter")?, "`axis_y.mirror`"),
    ];
    if let Some((_, what)) = unframed.iter().find(|(hit, _)| *hit) {
        return Err(diagnostic::refuse!("scatter: `frame: range` with {what} is not supported"));
    }
    match undrawn_axis {
        Some(what) => Err(diagnostic::refuse!("scatter: `frame: range` with {what} is not supported")),
        None => Ok(()),
    }
}

/// The first axis key the frame cannot honor: it draws its own line, end ticks and end labels on
/// `side`, in the line's ink, on a linear scale, with no grid, ruler or authored ticks.
fn undrawn_axis_key(ax: &AxisStyle, side: &str) -> Option<&'static str> {
    let line = ax.line.as_ref();
    let (line_color, line_width) = (line.and_then(|l| l.color.as_deref()), line.and_then(|l| l.width));
    let checks = [
        (ax.position.as_deref().is_some_and(|p| p != side), "position"),
        (line.and_then(|l| l.visible) != Some(true), "line.visible"),
        (ax.ticks.visible == Some(false), "ticks.visible"),
        (ax.ticks.length.is_some_and(|l| l != FRAME_TICK), "ticks.length"),
        (ax.ticks.color.as_deref().is_some_and(|c| Some(c) != line_color), "ticks.color"),
        (ax.ticks.width.is_some_and(|w| Some(w) != line_width), "ticks.width"),
        (ax.ticks.step.is_some(), "ticks.step"),
        (ax.labels.visible == Some(false), "labels.visible"),
        (ax.labels.expr.is_some(), "labels.expr"),
        (ax.labels.angle.is_some_and(|a| a != 0.0), "labels.angle"),
        (ax.labels.max_width.is_some(), "labels.max_width"),
        (ax.labels.align.as_deref().is_some_and(|a| a != if side == "bottom" { "center" } else { "right" }), "labels.align"),
        (ax.labels.flush.as_ref().is_some_and(|f| !f.is_null() && *f != Value::Bool(false)), "labels.flush"),
        (ax.labels.values.is_some(), "labels.values"),
        (ax.grid.visible == Some(true), "grid.visible"),
        (!ax.refit && ax.ruler.is_some(), "ruler"),
        (ax.transform() != d3rs::scale::Transform::Identity, "scale.continuous.type"),
        (ax.authored_scale_values().is_some(), "scale.values"),
    ];
    checks.iter().find(|(hit, _)| *hit).map(|&(_, key)| key)
}

/// The domain of a framed axis over the values `(lo, hi)`: d3's `nice` at count 10 over them,
/// joined to zero unless the axis turns zero off. An authored pair must contain the values and
/// already end on ticks.
fn frame_domain(id: &str, name: &str, ax: &AxisStyle, (lo, hi): (f64, f64)) -> Result<(f64, f64), String> {
    let refuse = |why: &str| Err(format!("chart {id}: `frame: range` needs a scale that ends on ticks, and {name} {why}"));
    let scale_num = |key: &str| ax.scale.get(key).and_then(Value::as_f64).filter(|v| *v != 0.0);
    if scale_num("padding").is_some() {
        return refuse("`scale.padding` pads it");
    }
    if scale_num("headroom").is_some() {
        return refuse("`scale.headroom` pads it");
    }
    let nice = ax.scale.get("nice").and_then(Value::as_bool);
    let (domain_min, domain_max) = if ax.refit { (None, None) } else { (ax.domain_min, ax.domain_max) };
    if nice == Some(false) || ((domain_min.is_some() || domain_max.is_some()) && nice != Some(true)) {
        return refuse("fits it to the data without `scale.nice: true`");
    }
    if let Some(pair) = ax.coerced_continuous_domain() {
        if !(pair.0 <= lo && hi <= pair.1) {
            return refuse("`scale.continuous.domain` does not contain the data");
        }
        return if d3rs::ticks::d3_nice(pair.0, pair.1, 10) == pair { Ok(pair) } else { refuse("`scale.continuous.domain` does not end on ticks") };
    }
    let (lo, hi) = if ax.is_zero_false() { (lo, hi) } else { (lo.min(0.0), hi.max(0.0)) };
    let (lo, hi) = (domain_min.map_or(lo, |edge| edge.min(lo)), domain_max.map_or(hi, |edge| edge.max(hi)));
    Ok(d3rs::ticks::d3_nice(lo, hi, 10))
}

struct FramedAxis<'a> {
    ax: &'a AxisStyle,
    domain: (f64, f64),
    extent: (f64, f64),
    labels: [String; 2],
    title: String,
}

impl<'a> FramedAxis<'a> {
    fn new(r: &ScatterResolved, ax: &'a AxisStyle, name: &str, (field, label): (&str, Option<&str>), values: &[f64]) -> Result<Self, String> {
        let extent = values.iter().fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), &v| (lo.min(v), hi.max(v)));
        let domain = frame_domain(&r.id, name, ax, extent)?;
        if domain.0 == domain.1 {
            let id = &r.id;
            return Err(diagnostic::refuse!("scatter chart {id}: `frame: range` is not supported when {name} holds one value"));
        }
        Ok(FramedAxis {
            ax,
            domain,
            extent,
            labels: end_labels(ax, extent)?,
            title: crate::common::axis_title_text(field, label, &r.field_labels)?,
        })
    }

    fn frac(&self, v: f64) -> f64 {
        (v - self.domain.0) / (self.domain.1 - self.domain.0)
    }

    fn title_shown(&self) -> bool {
        crate::common::axis_title_band(&self.ax.title, &self.title) > 0.0
    }

    fn label_widths(&self, fonts: &FontBook) -> Result<[f64; 2], String> {
        let font = &self.ax.labels.font;
        Ok([font.measure_weighted(fonts, &self.labels[0], font.size_or(11.0))?, font.measure_weighted(fonts, &self.labels[1], font.size_or(11.0))?])
    }
}

/// An axis's two end labels: each end in the authored format, else by the range-end rule.
fn end_labels(ax: &AxisStyle, (lo, hi): (f64, f64)) -> Result<[String; 2], String> {
    match ax.labels.format.as_ref() {
        Some(authored) => Ok([authored.paint_tick(lo)?, authored.paint_tick(hi)?]),
        None => range_end_labels(lo, hi),
    }
}

/// The most digits that told-apart ends may gain (numeral design, invariant 20).
const MAX_EXTRA_DIGITS: usize = 2;

/// The range-end rule over both ends of an axis. Two different values that print alike gain one
/// digit together, up to [`MAX_EXTRA_DIGITS`], keeping trailing zeros so both show the same
/// decimals; ends still alike after that print the plain rule.
fn range_end_labels(lo: f64, hi: f64) -> Result<[String; 2], String> {
    let plain = [range_end_label(lo, 0, true)?, range_end_label(hi, 0, true)?];
    if lo == hi || plain[0] != plain[1] {
        return Ok(plain);
    }
    for extra in 1..=MAX_EXTRA_DIGITS {
        let pair = [range_end_label(lo, extra, false)?, range_end_label(hi, extra, false)?];
        if pair[0] != pair[1] {
            return Ok(pair);
        }
    }
    Ok(plain)
}

/// The range-end rule for one end with no authored format: the house narrative SI register at two
/// significant digits, never fewer than the integer digits of the mantissa shown (80, 7.6k, 412k),
/// plus `extra` digits; trailing zeros are trimmed unless `trim` is off.
fn range_end_label(v: f64, extra: usize, trim: bool) -> Result<String, String> {
    let exponent = if v.abs() < 1.0 { 0.0 } else { (v.abs().log10() / 3.0).floor().min(8.0) * 3.0 };
    let mantissa_digits = (v.abs() / 10f64.powf(exponent)).log10().floor().max(0.0) as usize + 1;
    let spec = format!(".{}{}s", mantissa_digits.max(2) + extra, if trim { "~" } else { "" });
    let slot = FormatSlot { spec, raw: Some("number".into()), ..FormatSlot::default() };
    format_mark_label(Some(&slot), v, true)
}

fn text_node(role: Role, at: (f64, f64), anchor: &str, font: &Font, body: &str) -> Result<Node, String> {
    let color = font.color.as_deref().ok_or("scatter: a framed axis text has no resolved color")?;
    let attrs = Attrs::new()
        .set("text-anchor", anchor)
        .vega("font-family", font.family_or("sans-serif"))
        .num("font-size", font.size_or(11.0))
        .quick_some("font-style", font.style.as_deref())
        .set("font-weight", font.weight_str())
        .set("fill", color);
    Ok(Node::text(role, at, attrs, body))
}

enum Glyph {
    Solid(f64),
    Open(SeriesSymbol, f64),
}

fn solid(r: f64) -> PathData {
    PathData::new().move_to(r, 0.0).arc_to(r, true, -r, 0.0).arc_to(r, true, r, 0.0).close()
}

fn point_nodes(glyph: &Glyph, ink: &str, canvas: &str, halo: f64, at: (f64, f64), role: Role, extra: Attrs) -> [Node; 2] {
    let (d, filled, sw) = match glyph {
        Glyph::Solid(r) => (solid(*r), true, 0.0),
        Glyph::Open(symbol, r) => (symbol.path(*r), false, symbol.stroke_width()),
    };
    let ring = Attrs::new()
        .set("fill", if filled { canvas } else { "none" })
        .set("stroke", canvas)
        .num("stroke-width", sw + 2.0 * halo)
        .set("stroke-linejoin", "round")
        .set("pointer-events", "none");
    let mark = if filled {
        Attrs::new().set("fill", ink)
    } else {
        Attrs::new().set("fill", "none").set("stroke", ink).num("stroke-width", sw).set("stroke-linejoin", "round")
    };
    [Node::path(Role::Chrome, d.clone(), ring).translate(at.0, at.1), Node::path(role, d, mark.extend(extra)).translate(at.0, at.1)]
}

/// Which of an axis's two end labels (lower, higher) are drawn. The higher end stays; the lower one
/// goes when the two would overlap (`apart` false), with a warning unless they are one value.
fn ends_labeled(chart_id: &str, axis: &str, apart: bool, one_value: bool, diagnostics: &mut Vec<Diagnostic>) -> [bool; 2] {
    if !apart && !one_value {
        diagnostics.push(diagnostic::diag::warn_axis_end_label_dropped(chart_id, axis));
    }
    [apart, true]
}

fn crisp(v: f64) -> f64 {
    v.round() + 0.5
}

pub(super) fn layout(bound: &Bound, payload: &Payload, measure: &Measure) -> Result<Layout, Diagnostic> {
    let r = &bound.r;
    let fonts = measure.fonts;
    let whole: &[serde_json::Map<String, Value>] = r.whole_rows.as_deref().unwrap_or(&payload.rows);
    let drawn: Vec<&serde_json::Map<String, Value>> = bound.drawn.iter().map(|&i| &whole[i]).collect();
    let view = r.legend_view.as_ref();
    let shown_row = |row: &&serde_json::Map<String, Value>| r.color.as_deref().is_none_or(|cf| crate::legend_view::shown(view, &row_str(row, cf)));
    let rows: Vec<&serde_json::Map<String, Value>> = drawn.iter().copied().filter(shown_row).collect();
    let axis_rows = if rows.is_empty() { &drawn } else { &rows };
    let reading = |rows: &[&serde_json::Map<String, Value>], field: &str| -> Result<Vec<f64>, String> {
        rows.iter().map(|row| row_num(row, field).ok_or_else(|| format!("scatter: a drawn row has no number in {field:?}"))).collect()
    };
    let (xs, ys) = (reading(&rows, &r.x)?, reading(&rows, &r.y)?);
    let x = FramedAxis::new(r, &r.style.axis_x, "axis_x", (&r.x, r.x_label.as_deref()), &reading(axis_rows, &r.x)?)?;
    let y = FramedAxis::new(r, &r.style.axis_y, "axis_y", (&r.y, r.y_label.as_deref()), &reading(axis_rows, &r.y)?)?;
    let point = &r.style.point_mark;
    let halo = point.halo.ok_or_else(|| diagnostic::refuse!("scatter: `frame: range` with `marks.point.halo` unset is not supported (it rings every point in the canvas)"))?;
    let radius = crate::common::symbol_radius(point.size);
    let category_scale = r.color.as_deref().and_then(|cf| crate::common::category_scale_for(&r.category_colors, cf));
    let authored_symbols = r.style.series_symbols.as_deref().filter(|symbols| !symbols.is_empty());
    // A planned field's lone series keeps its symbol, so a filter that leaves one value does not turn it solid.
    let multi = bound.color_domain.len() > 1 || (category_scale.is_some() && authored_symbols.is_some() && !bound.color_domain.is_empty());
    let symbols = match (authored_symbols, multi) {
        (Some(symbols), true) => symbols,
        (_, true) => return Err("scatter: a framed chart with several series needs `series_symbols`".into()),
        (_, false) => &[],
    };
    // The board's color slot, so a value keeps its symbol across charts; with no plan, the rank by
    // average y, which orders the colors too so a series' color and symbol share a slot.
    let ranked = super::ranked_by_y(r, &bound.color_domain, r.whole_rows.as_deref().unwrap_or(&payload.rows));
    let own_order = ranked.as_deref().unwrap_or(&bound.color_domain);
    let symbol_of = |name: &str| -> Result<SeriesSymbol, String> {
        let slot = match category_scale {
            Some(scale) => ::bind::category_colors::slot_for(scale, name, usize::MAX)?,
            None => own_order.iter().position(|d| d == name).ok_or_else(|| format!("internal error — {name:?} not in this chart's own color domain"))?,
        };
        symbols.get(slot).copied().ok_or_else(|| {
            diagnostic::refuse!("scatter: a series past `series_symbols` is not supported ({name:?} takes slot {slot} of {} symbols)", symbols.len())
        })
    };
    let ink_of = |s: &str| -> Result<String, String> {
        if bound.color_domain.is_empty() {
            return Ok(r.style.single_series_fill.clone());
        }
        crate::common::series_color(s, own_order, &r.series_palette, category_scale, &crate::pattern::PatternPlan::default())
            .map(crate::pattern::Paint::into_color)
    };
    let glyph_of = |s: &str| -> Result<Glyph, String> {
        if multi {
            Ok(Glyph::Open(symbol_of(s)?, radius))
        } else {
            Ok(Glyph::Solid(radius))
        }
    };

    let key_font = &r.legend.label.font;
    let key: Vec<(SeriesSymbol, &String, f64)> = if multi && r.legend.visible {
        if r.legend.position.edge != "top" {
            return Err(diagnostic::refuse!("scatter: `frame: range` with a legend off the top edge is not supported").into());
        }
        let legend = &r.legend;
        let undrawn = [
            (legend.position.align != LegendAlign::Start, "position.align"),
            (legend.position.overlay, "position.overlay"),
            (legend.direction != "horizontal", "direction"),
            (legend.columns.is_some_and(|c| c != 0), "columns"),
            (legend.title.visible != Some(false), "title.visible"),
            (legend.symbol.shape.is_some(), "symbol.shape"),
            (legend.symbol.fill.is_some(), "symbol.fill"),
            (legend.limit.is_some_and(|n| (n as usize) < bound.color_domain.len()), "limit"),
        ];
        if let Some((_, key)) = undrawn.iter().find(|(hit, _)| *hit) {
            return Err(diagnostic::refuse!("scatter: `frame: range` with `legend.{key}` is not supported").into());
        }
        let order = super::legend_entry_order(&r.legend, &bound.color_domain, None, ranked.as_deref())?;
        order
            .into_iter()
            .map(|i| {
                let name = &bound.color_domain[i];
                Ok((symbol_of(name)?, name, key_font.measure_weighted(fonts, name, key_font.size_or(11.0))?))
            })
            .collect::<Result<_, String>>()?
    } else {
        Vec::new()
    };
    if let Some(cap) = r.legend.label.max_width {
        if key.iter().any(|(_, _, w)| *w > cap) {
            return Err(diagnostic::refuse!("scatter: `frame: range` with `legend.label.max_width` is not supported (a key name is wider than it)").into());
        }
    }
    let (title_font, key_size) = (&y.ax.title.font, key_font.size_or(11.0));
    let title_caps = title_font.cap_height(fonts, title_font.size_or(11.0))?;
    let key_base_over_tick = if y.title_shown() { TITLE_OVER_FRAME + title_caps + KEY_OVER_TITLE_CAPS } else { TITLE_OVER_FRAME };
    let above_tick = match (key.is_empty(), y.title_shown()) {
        (true, false) => 0.0,
        (false, _) => key_base_over_tick + key_size,
        (true, true) => TITLE_OVER_FRAME + title_font.size_or(11.0),
    }
    .max(y.ax.labels.font.size_or(11.0) * 0.5);

    let frame = ChartFrame::of(payload, r.background.as_str());
    let key_w: f64 = key.iter().map(|(_, _, w)| KEY_NAME_X + w).sum::<f64>() + KEY_ENTRY_GAP * key.len().saturating_sub(1) as f64;
    let title_w = if y.title_shown() { title_font.measure_weighted(fonts, &y.title, title_font.size_or(11.0))? } else { 0.0 };
    if key_w.max(title_w) > frame.avail_w() {
        let (what, w) = if key_w > title_w { ("the key", key_w) } else { ("the y-axis title", title_w) };
        return Err(diagnostic::refuse!("scatter: `frame: range` with {what} wider than its card is not supported ({w:.0} px of {:.0})", frame.avail_w()).into());
    }
    let title = FrameTitle::new(r.title.as_deref(), r.subtitle.as_deref(), r.style.title_font.as_ref(), r.title_style.as_ref(), frame.avail_w(), fonts)?;
    let mut diagnostics = title.truncations(&r.id);
    let (x_ws, y_ws) = (x.label_widths(fonts)?, y.label_widths(fonts)?);
    let y_gutter = FRAME_TICK + LABEL_GAP + y_ws[0].max(y_ws[1]);
    let x_title_font = &x.ax.title.font;
    let x_title_w = if x.title_shown() { x_title_font.measure_weighted(fonts, &x.title, x_title_font.size_or(11.0))? } else { 0.0 };
    let x_drop = FRAME_TICK + if x.title_shown() { X_TITLE_DROP } else { X_LABEL_DROP } + x.ax.labels.font.size_or(11.0) * MIDDLE_DROP;
    let reach = radius + halo;
    let m = frame.fit(&title, |pw, ph, b| {
        b.union(&Bounds::new(-y_gutter, (1.0 - y.frac(y.extent.1)) * ph - above_tick, pw, ph + x_drop));
        for (v, w) in [x.extent.0, x.extent.1].iter().zip(x_ws) {
            let cx = x.frac(*v) * pw;
            b.union(&Bounds::new(cx - w / 2.0, ph, cx + w / 2.0, ph));
        }
        b.union(&Bounds::new(pw / 2.0 - x_title_w / 2.0, ph, pw / 2.0 + x_title_w / 2.0, ph));
        b.union(&Bounds::new(-reach, -reach, pw + reach, ph + reach));
    });
    let (plot_left, plot_top) = frame.origin(&m);
    let (pw, ph) = (m.plot_w, m.plot_h);
    let px = |v: f64| plot_left + x.frac(v) * pw;
    let py = |v: f64| plot_top + (1.0 - y.frac(v)) * ph;
    let content_left = frame.pad.0;
    let canvas = r.canvas.as_str();
    let line_attrs = |ax: &AxisStyle| -> Result<Attrs, String> {
        let line = ax.line.as_ref().ok_or("scatter: a framed axis has no resolved line")?;
        let color = line.color.as_deref().ok_or("scatter: a framed axis line has no resolved color")?;
        let width = line.width.ok_or("scatter: a framed axis line has no resolved width")?;
        Ok(Attrs::new().set("stroke", color).num("stroke-width", width))
    };
    let (x_line, y_line) = (line_attrs(x.ax)?, line_attrs(y.ax)?);

    let mut body = title.nodes(m.title_pos, (plot_left, plot_top));
    let fy = crisp(plot_top + ph);
    let (xl, xh) = (crisp(px(x.extent.0)), crisp(px(x.extent.1)));
    body.push(Node::line(Role::Axis, (xl - 0.5, fy), (xh + 0.5, fy), x_line.clone()));
    let x_labeled = ends_labeled(&r.id, "axis_x", (xh - xl).abs() >= (x_ws[0] + x_ws[1]) / 2.0, x.extent.0 == x.extent.1, &mut diagnostics);
    for ((at, label), labeled) in [xl, xh].iter().zip(&x.labels).zip(x_labeled) {
        if xl != xh || labeled {
            body.push(Node::line(Role::Axis, (*at, fy), (*at, fy + FRAME_TICK), x_line.clone()));
        }
        if labeled {
            body.push(text_node(Role::Axis, (*at, fy + FRAME_TICK + X_LABEL_DROP), "middle", &x.ax.labels.font, label)?);
        }
    }
    if x.title_shown() {
        body.push(text_node(Role::Axis, (plot_left + pw / 2.0, fy + FRAME_TICK + X_TITLE_DROP), "middle", x_title_font, &x.title)?);
    }
    let fx = crisp(plot_left);
    let (yl, yh) = (crisp(py(y.extent.0)), crisp(py(y.extent.1)));
    body.push(Node::line(Role::Axis, (fx, yl + 0.5), (fx, yh - 0.5), y_line.clone()));
    let y_label_dy = y.ax.labels.font.size_or(11.0) * MIDDLE_DROP;
    let y_labeled = ends_labeled(&r.id, "axis_y", (yh - yl).abs() >= y.ax.labels.font.size_or(11.0), y.extent.0 == y.extent.1, &mut diagnostics);
    for ((at, label), labeled) in [yl, yh].iter().zip(&y.labels).zip(y_labeled) {
        if yl != yh || labeled {
            body.push(Node::line(Role::Axis, (fx - FRAME_TICK, *at), (fx, *at), y_line.clone()));
        }
        if labeled {
            body.push(text_node(Role::Axis, (fx - FRAME_TICK - LABEL_GAP, at + y_label_dy), "end", &y.ax.labels.font, label)?);
        }
    }
    if y.title_shown() {
        body.push(text_node(Role::Axis, (content_left, yh - TITLE_OVER_FRAME), "start", &y.ax.title.font, &y.title)?);
    }
    let key_base = yh - key_base_over_tick;
    let mut key_x = content_left;
    for (symbol, name, w) in &key {
        let center = (key_x + KEY_SYMBOL_X, key_base - key_font.size_or(11.0) * MIDDLE_DROP);
        let recede = view.and_then(|view| view.recede_of(name));
        let symbol_attrs = ::layout::contract::legend_symbol_attrs(false);
        let [ring, mut mark] = point_nodes(&Glyph::Open(*symbol, radius), &ink_of(name)?, canvas, halo, center, Role::Legend, symbol_attrs);
        let mut label = text_node(Role::Legend, (key_x + KEY_NAME_X, key_base), "start", key_font, name)?;
        if let Some(recede) = recede {
            mark.attrs = recede.recede_attrs(std::mem::take(&mut mark.attrs));
            label.attrs = recede.recede_attrs(std::mem::take(&mut label.attrs));
        }
        let label_attrs = ::layout::contract::legend_label_attrs(None, false);
        let label_attrs = match view {
            Some(view) => view.clickable(label_attrs, name),
            None => label_attrs,
        };
        body.extend([ring, mark, Node::group(Role::Legend, label_attrs, vec![label])]);
        key_x += KEY_NAME_X + w + KEY_ENTRY_GAP;
    }

    let tooltip_labels = !r.style.tooltip_format.spec.is_empty();
    let x_tooltip_format = r.x_tooltip_row_format();
    let datum_link = r.link.as_deref().map(|template| crate::href::DatumLink {
        template,
        open: r.link_open,
        x: crate::href::LinkField { name: &r.x, text: crate::href::LinkText::Raw },
        y: Some(crate::href::LinkField::raw(&r.link_y)),
        color: r.link_color.as_deref().map(crate::href::LinkField::raw),
        context: r.link_context.as_ref(),
    });
    // Points after the frame, so a point on the frame lies on it, its ring between.
    for (datum, (row, (&xv, &yv))) in rows.iter().zip(xs.iter().zip(&ys)).enumerate() {
        let s = r.color.as_deref().map(|cf| row_str(row, cf)).unwrap_or_default();
        let aria = if tooltip_labels {
            let extra = crate::families::tooltip_extra::rows_for(&r.tooltip, row, &payload.schema.rows)?;
            let tooltip = crate::contract::ScatterTooltip {
                header: r.color.as_deref().map(|_| s.as_str()),
                y_title: &y.title,
                y_value: crate::contract::ScatterValue::Quantitative(yv, &r.style.tooltip_format),
                x_title: &x.title,
                x_value: crate::contract::ScatterValue::Quantitative(xv, &x_tooltip_format),
                extra: &extra,
            };
            Some(crate::contract::scatter_aria_label(&tooltip)?)
        } else {
            None
        };
        let role = Role::Mark { layer: 0, series: Some(s.clone()).filter(|s| !s.is_empty()), datum: Some(datum) };
        let aria_attrs = ::layout::contract::aria_attrs(aria.as_deref(), ::layout::contract::ItemRole::Point)?;
        let [ring, mark] = point_nodes(&glyph_of(&s)?, &ink_of(&s)?, canvas, halo, (px(xv), py(yv)), role, aria_attrs);
        let href = datum_link.as_ref().map(|link| link.href(row)).transpose()?;
        let mark = crate::href::linked(r.link_open.anchor(href.as_deref()), mark);
        body.push(ring);
        body.push(if aria.is_some() { ::layout::contract::mark_group("symbol", "marks", vec![mark])? } else { mark });
    }
    let data_box = Rect { x: plot_left, y: plot_top, width: pw, height: ph };
    let canvas_rect = Rect { x: 0.0, y: 0.0, width: frame.w, height: frame.h };
    Ok(Layout { insets: canvas_rect.insets_of(&data_box), data_box, nodes: vec![frame.svg(body)], diagnostics, title_pos: m.title_pos })
}
