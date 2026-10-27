//! Histogram layout: every pixel decision. Vega's `autosize: fit` negotiation runs over the
//! plot rect, both axes' label ink and the legend's box; the bars are Vega-Lite's binned rect
//! positioning over the x scale. Axis and chart titles that overflow are cut here and returned
//! in `Layout.diagnostics`.

use scene::{Attrs, Node};

use super::bind::{Bound, LegendPaint};
use super::Resolved;
use crate::common::{format_y_label, XEdge};
use crate::diagnostic::Diagnostic;
use crate::families::axis_caption::value_axis_caption;
use crate::families::cartesian::{vl_default_tick_count, LinearScale};
use crate::families::color_scale::GradientAt;
use crate::families::heatmap::{flat_label, rotated_label, svg_text_anchor, tick_line, LabelFace};
use crate::families::legend;
use crate::frame::{ChartFrame, FrameTitle};
use crate::layout::{IntrinsicWidth, Layout, Rect};
use crate::vega_fit;
use measure::Measure;

/// The decimals a histogram's axis and bar coordinates print with.
const PLACES: usize = 3;

/// A histogram fills the width it is given: nothing to hug.
pub fn intrinsic(_bound: &Bound, _measure: &Measure) -> Result<Option<IntrinsicWidth>, Diagnostic> {
    Ok(None)
}

/// A histogram draws in the slot sizing hands it: that slot's height is its natural height at
/// any width, and [`layout`] draws the cell it is given.
pub fn height_for_width(bound: &Bound, _width: f64, _measure: &Measure) -> Result<f64, Diagnostic> {
    Ok(bound.height)
}

/// `(left_edge_px, right_edge_px)` of the rect Vega draws for one bin, given
/// its pixel span `[bin_start_px, bin_end_px]`: the time bucket's bar law
/// (`bucket_bar_extent`).
fn bar_edges(bin_start_px: f64, bin_end_px: f64, band_width: f64) -> (f64, f64) {
    let (left, width) = crate::families::bar::bucket_bar_extent(bin_start_px, bin_end_px, band_width);
    (left, left + width)
}

/// One stack segment, `(top, bottom)` px at `(x, width)`. A bin's stack is
/// clipped to one rect whose top corners take radius `r` from `stack_top`, so a
/// segment reaching into the curve follows its arc. `None` is a null color's
/// mark, which Vega leaves unfilled under its root group's `fill: none`.
fn stack_segment(
    (x, w): (f64, f64),
    (top, bottom): (f64, f64),
    (stack_top, r): (f64, f64),
    fill: Option<&str>,
    aria: Attrs,
) -> Node {
    let fill = fill.unwrap_or("none");
    let h = bottom - top;
    if r <= 0.0 || top >= stack_top + r {
        return Node::Rect(
            Attrs::new()
                .set("x", format!("{x:.4}"))
                .set("y", format!("{top:.4}"))
                .set("width", format!("{w:.4}"))
                .set("height", format!("{h:.4}"))
                .set("fill", fill)
                .set("stroke-width", "0")
                .extend(aria),
        );
    }
    // The clip's corner circles are centered `r` below the stack top, `r` in
    // from each side; a segment's side runs along the circle down to the
    // curve's end `stack_top + r` and straight below it.
    let arc_end = bottom.min(stack_top + r);
    let side = |y: f64| r - (r * r - (stack_top + r - y).powi(2)).max(0.0).sqrt();
    let x2 = x + w;
    let (start, down) = if bottom > arc_end {
        (format!("M {x:.4},{bottom:.4} V {arc_end:.4}"), format!(" V {bottom:.4}"))
    } else {
        (format!("M {:.4},{arc_end:.4}", x + side(arc_end)), String::new())
    };
    let d = format!(
        "{start} A {r:.4},{r:.4} 0 0,1 {:.4},{top:.4} H {:.4} A {r:.4},{r:.4} 0 0,1 {:.4},{arc_end:.4}{down} Z",
        x + side(top),
        x2 - side(top),
        x2 - side(arc_end),
    );
    Node::Path(Attrs::new().set("d", d).set("fill", fill).set("stroke-width", "0").extend(aria))
}

pub fn layout(bound: &Bound, cell: Rect, measure: &Measure) -> Result<Layout, Diagnostic> {
    let r: &Resolved = &bound.r;
    let fonts = measure.fonts;
    let ax_x = &r.style.axis_x;
    let ax_y = &r.style.axis_y;
    let x_edge = ax_x.x_edge();
    let val_on_right = bound.val_on_right;
    let ghost = &bound.ghost;
    let mirror_active = ghost.is_some();
    let measure_ghost =
        |label: &str| fonts.measure_shaped(label, ax_y.labels.font.family_or("sans-serif"), ax_y.labels.font.size_or(11.0));
    let (bin_start, bin_stop) = bound.x_extent;
    let (max_count, y_domain_max) = (bound.max_count, bound.y_domain_max);
    let (x_ticks_domain, x_tick_labels) = (&bound.x_ticks, &bound.x_labels);
    let mut diagnostics = Vec::new();

    // The chart's OWN effective background — see `Resolved::background`.
    let frame = ChartFrame::at_view(
        cell.width,
        cell.height,
        bound.padding.as_ref(),
        bound.board_style.as_ref(),
        r.background.as_str(),
        crate::ViewScale::Continuous,
    )?;

    let y_fmt = ax_y.labels.format.as_ref();
    let unbaked_y_ticks = |plot_h: f64| -> Vec<f64> {
        // `_emit_histogram`'s `tickCount` expression, `max(1, min(ceil(height /
        // 40), domain('y')[1]))`: never more ticks than the
        // domain's top count. A nice domain over whole counts tops out whole.
        let count = (vl_default_tick_count(plot_h) as f64)
            .min(y_domain_max)
            .max(1.0) as usize;
        crate::families::cartesian::unbaked_ticks(ax_y, 0.0, y_domain_max, count)
    };

    let title = FrameTitle::new(
        r.title.as_deref(),
        r.subtitle.as_deref(),
        r.style.title_font.as_ref(),
        r.title_style.as_ref(),
        frame.avail_w(),
        fonts,
    )?;
    diagnostics.extend(title.truncations(&r.id));

    let swatch = legend::LegendSymbol::Square(r.style.mark.border.color.as_deref().unwrap_or("none"));
    let symbol_entries: Vec<legend::LegendEntry> = match &bound.legend {
        Some(LegendPaint::Symbols(entries)) if r.legend.visible => entries
            .iter()
            .map(|(label, fill)| legend::LegendEntry { label, fill, symbol: Some(swatch), series_key: None, recede: None })
            .collect(),
        _ => Vec::new(),
    };
    let symbol_legend = (r.legend.visible
        && matches!(&bound.legend, Some(LegendPaint::Symbols(_)))
        && !(symbol_entries.is_empty() && bound.legend_title.is_empty()))
    .then(|| legend::vega_legend(&symbol_entries, false, &bound.legend_title, &r.legend, fonts))
    .transpose()?;
    let gradient_color = match &bound.legend {
        Some(LegendPaint::Gradient(color)) if r.legend.visible => Some(color),
        _ => None,
    };
    let gradient_id = bound.gradient_id.as_str();
    // The legend's origin in plot coordinates, set on every fit pass (the last
    // is the fitted layout).
    let legend_xy = std::cell::Cell::new((0.0, 0.0));

    let x_font_size = ax_x.labels.font.size_or(11.0);
    let x_family = ax_x.labels.font.family_or("sans-serif");
    let x_label_pad = ax_x.labels.padding.unwrap_or(6.0);
    let x_axis_offset = ax_x.ticks.offset.unwrap_or(0.0);
    let x_ticks_visible = ax_x.ticks.visible.unwrap_or(true);
    let x_tick_len = ax_x.ticks.length.unwrap_or(6.0);
    let x_tick_eff = if x_ticks_visible { x_tick_len } else { 0.0 };
    let x_limit = ax_x.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    let x_label_ws = measure.widths(x_tick_labels, x_family, x_font_size)?;

    let y_font_size = ax_y.labels.font.size_or(11.0);
    let y_family = ax_y.labels.font.family_or("sans-serif");
    let y_label_pad = ax_y.labels.padding.unwrap_or(6.0);
    let y_ticks_visible = ax_y.ticks.visible.unwrap_or(true);
    let y_tick_len = ax_y.ticks.length.unwrap_or(6.0);
    let y_tick_eff = if y_ticks_visible { y_tick_len } else { 0.0 };
    let y_limit = ax_y.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    // The measured shape replaces the themed `labelPadding` with the
    // cover-ladder estimate over the authored domain — the same law `bar.rs` and `line.rs` apply, one function.
    let measured_label_pad = if bound.own_side_measured {
        Some(crate::common::estimated_tick_label_gutter(
            fonts,
            y_family,
            y_font_size,
            y_limit,
            ax_y.tick_label.as_ref(),
            y_fmt,
            ax_y,
            0.0,
            max_count,
        )?)
    } else {
        None
    };
    // Vega anchors a label `tick + labelPadding` from the axis line. The
    // default alignment grows its ink away from the plot, so the gutter adds
    // the widest label; the own-side one grows back toward the plot, so the
    // anchor is the gutter's far edge and the ink adds nothing.
    let y_label_gap = measured_label_pad.unwrap_or(y_label_pad);
    let y_labels_flipped = measured_label_pad.is_some();
    let y_labels_start = val_on_right != y_labels_flipped;
    let y_angle = ax_y.labels.angle.unwrap_or(0.0);
    // Vega-Lite's default label anchor for a rotated y axis; a measured own-side
    // `labelAlign` is authored, so only the baseline follows the angle.
    let y_angle_norm = vega_fit::normalize_angle(y_angle);
    let (vl_align, y_baseline) = vega_fit::vl_y_label_anchor(y_angle_norm, !val_on_right);
    let y_align = if y_labels_flipped || y_angle == 0.0 {
        if y_labels_start { "left" } else { "right" }
    } else {
        vl_align
    };
    // The ghost keeps a measured own-side `labelAlign` verbatim (it is authored,
    // `mirror_axis` copies the primary's axis); otherwise it takes
    // Vega-Lite's default for its own edge.
    let (ghost_default_align, ghost_baseline) = vega_fit::vl_y_label_anchor(y_angle_norm, val_on_right);
    let ghost_align = if y_labels_flipped { y_align } else { ghost_default_align };
    // How far the labels' ink reaches past their anchor, away from the plot
    // (`s = -bounds.x1` for a left axis): the widest label, or the widest
    // rotated ink about each label's own anchor.
    let y_ink_gutter = |widths: &[f64], ticks: &[f64], ph: f64| -> f64 {
        if y_labels_flipped {
            return 0.0;
        }
        if y_angle == 0.0 {
            return widths.iter().cloned().fold(0.0_f64, f64::max).min(y_limit);
        }
        widths
            .iter()
            .zip(ticks)
            .map(|(&w, &tv)| {
                let y = ph * (1.0 - tv / y_domain_max);
                let ink = vega_fit::text_ink(0.0, y, w.min(y_limit), y_font_size, y_align, y_baseline).rotate(y_angle, 0.0, y);
                if val_on_right { ink.x2 } else { -ink.x1 }
            })
            .fold(0.0_f64, f64::max)
    };

    let (x_title_paint, x_cut) =
        crate::common::axis_title_lines(&r.id, "x_label", &bound.x_title, &ax_x.title.font, frame.w, 1.0, fonts)?;
    diagnostics.extend(x_cut);
    let y_title_paint = bound.y_title.as_str();
    let y_title_extra = crate::common::y_title_extra(&ax_y.title, y_title_paint);
    let x_title_extra = crate::common::x_title_extra(&ax_x.title, &x_title_paint);
    let x_title_w = crate::common::widest_title_line(&ax_x.title, &x_title_paint, fonts)?;
    let y_title_w = crate::common::widest_title_line(&ax_y.title, y_title_paint, fonts)?;

    let corner_r = r.style.mark.border.radius;
    let band_width = r.style.mark.band_width;

    let mut fit_err: Option<String> = None;
    let m = frame.fit(&title, |pw, ph, b| {
        if fit_err.is_some() {
            return;
        }
        let ink = |b: &mut vega_fit::Bounds| -> Result<(), String> {
            let y_ticks = unbaked_y_ticks(ph);
            let y_labels: Vec<String> = y_ticks
                .iter()
                .enumerate()
                .map(|(i, &tv)| ax_y.labels.apply_expr("y", tv, format_y_label(tv, None, None, y_fmt, i, y_ticks.len())?, i, y_ticks.len()))
                .collect::<Result<_, _>>()?;
            let y_label_ws = measure.shaped_widths(&y_labels, y_family, y_font_size)?;
            let y_gutter = y_ink_gutter(&y_label_ws, &y_ticks, ph);
            let ghost_ws = ghost.as_ref().map_or(Ok(Vec::new()), |g| g.widths(&y_ticks, &y_labels, &y_label_ws, measure_ghost))?;
            // The FREE edge (no value axis) still keeps a minimum
            // breathing room past the plot (`AXIS_FREE_EDGE_PAD`, same
            // small reservation `bar.rs`'s own axis-free edge uses) —
            // probed: the reference's free-edge margin is 2px past an
            // edge-flush label whose own ink stays inside the plot, so
            // this floor is the only source of it. The gutter side
            // mirrors around `pw` when the value axis is on the right.
            let gutter_reserve = y_tick_eff + y_label_gap + y_gutter + y_title_extra;
            if val_on_right {
                b.union(&vega_fit::Bounds::new(-crate::common::AXIS_FREE_EDGE_PAD, 0.0, pw + gutter_reserve, ph));
            } else {
                b.union(&vega_fit::Bounds::new(-gutter_reserve, 0.0, pw + crate::common::AXIS_FREE_EDGE_PAD, ph));
            }
            if y_title_extra > 0.0 {
                let x = if val_on_right { pw + gutter_reserve } else { -gutter_reserve };
                b.union(&vega_fit::Bounds::new(x, ph / 2.0 - y_title_w / 2.0, x, ph / 2.0 + y_title_w / 2.0));
            }
            if mirror_active {
                let x = if val_on_right { -(y_tick_eff + y_label_gap) } else { pw + y_tick_eff + y_label_gap };
                for (&lw, &tv) in ghost_ws.iter().zip(&y_ticks) {
                    let y = ph * (1.0 - tv / y_domain_max);
                    let ink = vega_fit::text_ink(x, y, lw.min(y_limit), y_font_size, ghost_align, ghost_baseline);
                    b.union(&if y_angle == 0.0 { ink } else { ink.rotate(y_angle, x, y) });
                }
            }
            for ((&lw, &tv), lbl) in y_label_ws.iter().zip(&y_ticks).zip(&y_labels) {
                let x = if val_on_right {
                    pw + y_tick_eff + y_label_gap
                } else {
                    -(y_tick_eff + y_label_gap)
                };
                let y = ph * (1.0 - tv / y_domain_max);
                if y_angle == 0.0 {
                    b.union(&vega_fit::text_ink_rows(x, y, lw.min(y_limit), y_font_size, y_align, "middle", crate::text::line_count(lbl)));
                } else {
                    let ink = vega_fit::text_ink(x, y, lw.min(y_limit), y_font_size, y_align, y_baseline);
                    b.union(&ink.rotate(y_angle, x, y));
                }
            }
            let row_y = x_edge.at(ph, x_axis_offset + x_tick_eff + x_label_pad);
            let pass_x_scale = LinearScale::new(bin_start, bin_stop, 0.0, pw);
            for ((&tv, &lw), lbl) in x_ticks_domain.iter().zip(x_label_ws.iter()).zip(x_tick_labels) {
                let pos = pass_x_scale.position(tv);
                let align = crate::families::line::flush_align(pos, pw);
                b.union(&vega_fit::text_ink_rows(pos, row_y, lw.min(x_limit), x_font_size, align, x_edge.baseline(), crate::text::line_count(lbl)));
            }
            // The x axis's own vertical extent, plot rect included.
            let (mut x_axis_y1, mut x_axis_y2) = match x_edge {
                XEdge::Bottom => (0.0, b.y2.max(ph)),
                XEdge::Top => (b.y1.min(0.0), ph),
            };
            if x_title_extra > 0.0 {
                match x_edge {
                    XEdge::Bottom => x_axis_y2 = ph + (b.y2 - ph).max(0.0) + x_title_extra,
                    XEdge::Top => x_axis_y1 = -((-b.y1).max(0.0) + x_title_extra),
                }
                b.union(&vega_fit::Bounds::new(pw / 2.0 - x_title_w / 2.0, x_axis_y1, pw / 2.0 + x_title_w / 2.0, x_axis_y2));
            }
            // The legend anchors on Vega's `xBounds`/`yBounds`: the plot
            // rect and its axes of that orientation.
            let legend_size = match (&symbol_legend, gradient_color) {
                (Some(lg), _) => Some((lg.box_w, lg.box_h())),
                (None, Some(color)) => {
                    let at = GradientAt::new(&r.legend, r.color_format.as_ref(), color, (pw, ph))?;
                    let legend = at.legend(&r.legend, color, gradient_id, &bound.legend_title, fonts)?;
                    Some((legend.box_width(at.length), legend.height(at.length)))
                }
                (None, None) => None,
            };
            if let Some(size) = legend_size {
                let mut x_bounds = if val_on_right { (0.0, pw + gutter_reserve) } else { (-gutter_reserve, pw) };
                if mirror_active {
                    let far = if val_on_right { -(y_tick_eff + y_label_gap) } else { pw + y_tick_eff + y_label_gap };
                    let reach = ghost_ws.iter().fold(0.0_f64, |w, &lw| w.max(lw.min(y_limit)));
                    x_bounds = if val_on_right { (far - reach, x_bounds.1) } else { (x_bounds.0, far + reach) };
                }
                let origin = legend::legend_origin(&r.legend.position, x_bounds, (x_axis_y1, x_axis_y2), (pw, ph), size);
                legend_xy.set(origin);
                if let Some(reach) = legend::legend_fit_bounds(&r.legend.position, origin, size) {
                    b.union(&reach);
                }
            }
            // Bar rect ink (top corner radius only — never exceeds the
            // straight rect bounds).
            let y0 = ph;
            for bin in &bound.bins {
                let x_scale = LinearScale::new(bin_start, bin_stop, 0.0, pw);
                let (left, right) = bar_edges(x_scale.position(bin.start), x_scale.position(bin.end), band_width);
                let top = ph * (1.0 - bin.count as f64 / y_domain_max);
                b.union(&vega_fit::Bounds::new(left, top.min(y0), right, top.max(y0)));
            }
            Ok(())
        };
        if let Err(e) = ink(b) {
            fit_err = Some(e);
        }
    });
    if let Some(e) = fit_err {
        return Err(e.into());
    }
    let (plot_w, plot_h) = (m.plot_w, m.plot_h);
    let x_scale = LinearScale::new(bin_start, bin_stop, 0.0, plot_w);
    if crate::labels_expr::view::active() {
        use crate::labels_expr::view::{register_axis, register_linear, AxisSpec, Orient, Span};
        register_linear("x", bin_start, bin_stop, Span::Width);
        register_linear("y", 0.0, y_domain_max, Span::Height);
        let x_spec = AxisSpec { orient: Orient::Bottom, offset: x_tick_eff + x_label_pad, shift: 0.0, align: None, baseline: None, angle: 0.0, flush: Some(1.0), style: ax_x.labels.item_style()? };
        if ax_x.labels.upright() {
            register_axis("x", x_spec);
        }
        if y_angle == 0.0 {
            let orient = if val_on_right { Orient::Right } else { Orient::Left };
            let y_spec = AxisSpec { orient, offset: y_tick_eff + y_label_gap, shift: 0.0, align: Some(y_align), baseline: None, angle: 0.0, flush: None, style: ax_y.labels.item_style()? };
            register_axis("y", y_spec);
        }
    }
    let y_ticks = unbaked_y_ticks(plot_h);
    let y_labels: Vec<String> = y_ticks
        .iter()
        .enumerate()
        .map(|(i, &tv)| ax_y.labels.apply_expr("y", tv, format_y_label(tv, None, None, y_fmt, i, y_ticks.len())?, i, y_ticks.len()))
        .collect::<Result<_, _>>()?;
    let y_label_ws = measure.shaped_widths(&y_labels, y_family, y_font_size)?;
    let y_gutter = y_ink_gutter(&y_label_ws, &y_ticks, plot_h);
    let y_pos = |count: f64| plot_h * (1.0 - count / y_domain_max);

    let (group_x, group_y) = frame.origin(&m);
    let mut body = title.nodes(m.title_pos, (group_x, group_y));
    let mut plot = Vec::new();

    if ax_y.grid.visible.unwrap_or(false) {
        let gc = crate::common::required_ink(ax_y.grid.color.as_deref(), "histogram", "axis_y.grid.color")?;
        let gw = ax_y.grid.width.unwrap_or(1.0);
        let go = ax_y.grid.opacity.unwrap_or(1.0);
        // The gridline on the axis edge also reaches under the outermost tick strokes.
        let (floor_x1, floor_x2) =
            crate::common::floor_rule_span(plot_w, ax_x, x_ticks_visible, x_ticks_domain.iter().map(|&tv| x_scale.position(tv).round() + 0.5));
        let lines = y_ticks
            .iter()
            .map(|&tv| {
                let gy = y_pos(tv).round() + 0.5;
                let (x1, x2) = if crate::common::rule_on_edge([gy - 0.5], x_edge.edge_y(plot_h)) {
                    (format!("{:.3}", floor_x1.min(0.5)), format!("{:.3}", floor_x2.max(plot_w + 0.5)))
                } else {
                    ("0.5".to_string(), format!("{:.3}", plot_w + 0.5))
                };
                Node::Line(
                    Attrs::new()
                        .set("x1", x1)
                        .set("y1", format!("{gy:.3}"))
                        .set("x2", x2)
                        .set("y2", format!("{gy:.3}"))
                        .set("stroke", gc)
                        .set("stroke-width", gw.to_string())
                        .set("opacity", go.to_string()),
                )
            })
            .collect();
        plot.push(crate::contract::grid_group(lines));
    }

    // Bars: an empty bin has no segments.
    let mut marks = Vec::new();
    // The mirror's invisible rule layer carries each segment's label at its own count (the rules do not stack).
    let mut ghosts: Vec<(&str, f64, f64, &str, Option<&str>)> = Vec::new();
    for bin in bound.bins.iter().filter(|b| b.count > 0) {
        let (left, right) = bar_edges(x_scale.position(bin.start), x_scale.position(bin.end), band_width);
        // One rounded clip over the whole stack: it rounds the stack's top
        // corners, and a segment inside the curve follows it.
        let stack_h = plot_h - y_pos(bin.count as f64);
        let corner = corner_r.min((right - left) / 2.0).min(stack_h / 2.0);
        let stack_top = y_pos(bin.count as f64);
        let mut below = 0;
        for segment in &bin.segments {
            let bottom = y_pos(below as f64);
            below += segment.n;
            let top = y_pos(below as f64);
            let Some(mark) = &segment.mark else { continue };
            let aria = crate::contract::aria_attrs(Some(&mark.label), crate::contract::ItemRole::Bar)?;
            let rect = stack_segment((left, right - left), (top, bottom), (stack_top, corner), mark.fill.as_deref(), aria);
            marks.push(crate::href::linked(r.link_open.anchor(mark.href.as_deref()), rect));
            if mirror_active {
                let ink = match mark.fill.as_deref() {
                    Some(fill) if bound.colored => fill,
                    _ => crate::common::required_ink(ax_y.labels.font.color.as_deref(), "histogram", "axis_y.labels.font.color")?,
                };
                ghosts.push((
                    &mark.label,
                    (x_scale.position(bin.start) + x_scale.position(bin.end)) / 2.0,
                    y_pos(segment.n as f64),
                    ink,
                    mark.href.as_deref(),
                ));
            }
        }
    }
    plot.push(crate::contract::mark_group("rect", "marks", marks)?);
    if mirror_active {
        let rules: Vec<crate::contract::GhostRule> = ghosts
            .iter()
            .map(|&(label, x, y, stroke, href)| crate::contract::GhostRule {
                aria: Some(label),
                x,
                y,
                stroke,
                anchor: r.link_open.anchor(href),
            })
            .collect();
        plot.push(crate::contract::ghost_layer(&rules, crate::contract::GhostRun::Down(plot_h))?);
    }

    let (lx, ly) = legend_xy.get();
    if let Some(lg) = &symbol_legend {
        let (dx, dy) = legend::legend_slide(&r.id, &r.legend.position, lg, (plot_w, plot_h))?;
        plot.extend(legend::drawn_symbol_legend(&symbol_entries, lg, &r.legend, swatch, lx + dx, ly + dy, false));
    } else if let Some(color) = gradient_color {
        let at = GradientAt::new(&r.legend, r.color_format.as_ref(), color, (plot_w, plot_h))?;
        let legend = at.legend(&r.legend, color, gradient_id, &bound.legend_title, fonts)?;
        let block = (legend.box_width(at.length), legend.height(at.length));
        let (dx, dy) = legend::slide_block(&r.id, &r.legend.position, block, (plot_w, plot_h))?;
        plot.extend(legend.nodes(lx + dx, ly + dy, at.length));
    }

    // X ticks + labels.
    // Vega's crisp half pixel shifts both axis groups by +0.5 in both directions
    // (`docs/laws-shared.md#tickround`): a tick rounds its own position first, a
    // label keeps its exact scale position and inherits the group's shift.
    let x_group_y = x_edge.at(plot_h, x_axis_offset) + 0.5;
    let mut x_items = Vec::new();
    if x_ticks_visible {
        let xs = x_ticks_domain.iter().map(|&tv| x_scale.position(tv).round() + 0.5);
        let end_y = x_group_y + x_edge.sign() * x_tick_len;
        x_items.extend(crate::common::x_tick_marks(&ax_x.ticks, None, &[], xs, (x_edge.edge_y(plot_h), end_y), PLACES)?);
    }
    let x_label_color = ax_x.labels.font.color.as_deref().unwrap_or("#626366");
    let x_label_weight = ax_x.labels.font.weight_str();
    let x_label_y =
        x_group_y + x_edge.sign() * (x_tick_eff + x_label_pad) + measure::baseline_shift(x_edge.baseline(), x_font_size);
    for (&tv, lbl) in x_ticks_domain.iter().zip(x_tick_labels.iter()) {
        let pos = x_scale.position(tv);
        // labelOverlap: allow — every tick always draws; the
        // edges still flush-anchor (VL's default labelFlush on a
        // continuous scale).
        let face = LabelFace {
            places: PLACES,
            anchor: crate::families::line::flush_anchor_px(pos, plot_w),
            family: x_family,
            size: x_font_size,
            weight: &x_label_weight,
            fill: x_label_color,
        };
        // `x_label_y` carries the crisp shift on the constant coordinate; this is the
        // same term on the label's own varying position.
        x_items.push(flat_label((pos + 0.5, x_label_y), &face, false, &ax_x.labels.clip(lbl, fonts)?));
    }
    let x_label_rows = x_tick_labels.iter().map(|l| crate::text::line_count(l)).max().unwrap_or(1);
    x_items.extend(crate::common::axis_title_flat(
        &x_title_paint,
        &ax_x.title,
        plot_w / 2.0,
        x_label_y + (x_label_rows - 1) as f64 * crate::text::line_height(x_font_size),
        x_font_size,
        Some(crate::contract::LabelKey::X),
        x_edge,
    ));
    plot.extend(crate::contract::axis_group(
        crate::contract::AxisChannel::X,
        &value_axis_caption(&x_title_paint, &ax_x.title, ax_x, (bin_start, bin_stop), false)?,
        (0.5, x_group_y),
        x_items,
    )?);

    // Y ticks + labels — left or right, per `val_on_right`: a right-side value axis
    // mirrors the left one exactly, with no breathing room.
    let y_caption = value_axis_caption(y_title_paint, &ax_y.title, ax_y, (0.0, y_domain_max), false)?;
    let y_ticks_nodes = |(tick_x1, tick_x2): (f64, f64)| -> Result<Vec<Node>, String> {
        if y_tick_eff <= 0.0 {
            return Ok(Vec::new());
        }
        let tc = crate::common::required_ink(ax_y.ticks.color.as_deref(), "histogram", "axis_y.ticks.color")?;
        let tw = ax_y.ticks.width.unwrap_or(1.0);
        Ok(y_ticks
            .iter()
            .map(|&tv| {
                let gy = y_pos(tv).round() + 0.5;
                tick_line(PLACES, (tick_x1, gy), (tick_x2, gy), tc, tw)
            })
            .collect())
    };
    let y_label_color = ax_y.labels.font.color.as_deref().unwrap_or("#626366");
    let y_label_weight = ax_y.labels.font.weight_str();
    let y_label = |x: f64, (align, baseline): (&str, &str), tv: f64, lbl: &str| -> Result<Node, String> {
        let face = LabelFace {
            places: PLACES,
            anchor: svg_text_anchor(align),
            family: y_family,
            size: y_font_size,
            weight: &y_label_weight,
            fill: y_label_color,
        };
        let text = ax_y.labels.clip(lbl, fonts)?;
        // +0.5 on the label's varying position; `y_label_x` carries it on the constant one.
        let y = y_pos(tv) + 0.5;
        Ok(if y_angle != 0.0 {
            rotated_label((x, y, y_angle, measure::baseline_shift(baseline, y_font_size)), &face, (&text, ""), false)
        } else {
            flat_label((x, y + measure::baseline_shift(baseline, y_font_size)), &face, false, &text)
        })
    };
    let y_label_x = if val_on_right {
        plot_w + y_tick_eff + y_label_gap + 0.5
    } else {
        -(y_tick_eff + y_label_gap) + 0.5
    };
    let mut y_items = y_ticks_nodes(if val_on_right { (plot_w, plot_w + y_tick_eff) } else { (-y_tick_eff, 0.0) })?;
    for (&tv, lbl) in y_ticks.iter().zip(y_labels.iter()) {
        y_items.push(y_label(y_label_x, (y_align, if y_angle == 0.0 { "middle" } else { y_baseline }), tv, lbl)?);
    }
    // `label_edge_x` is the gutter's OUTER edge: `y_label_x`'s own INNER
    // edge (the label anchor, above) plus/minus the gutter width, carrying
    // the same +0.5 crisp shift. Under the measured own-side shape the
    // gutter is 0, so the outer edge is the anchor itself (tick plus
    // `labelPadding`). Line's rail-concat left-axis title uses this identical
    // `0.5 - pad - tick - gutter` shape (`line.rs`'s `Shape::RailConcat`
    // arm); the right-axis case mirrors it around `plot_w` with `sign: 1.0`.
    let (label_edge_x, sign) = if val_on_right {
        (plot_w + 0.5 + y_tick_eff + y_label_gap + y_gutter, 1.0)
    } else {
        (0.5 - y_tick_eff - y_label_gap - y_gutter, -1.0)
    };
    y_items.extend(crate::common::axis_title_rotated(
        y_title_paint,
        &ax_y.title,
        label_edge_x,
        plot_h / 2.0,
        sign,
        Some(crate::contract::LabelKey::Y),
    ));
    plot.extend(crate::contract::axis_group(
        crate::contract::AxisChannel::Y,
        &y_caption,
        (if val_on_right { plot_w + 0.5 } else { 0.5 }, 0.0),
        y_items,
    )?);
    if mirror_active {
        let mut ghost_items = y_ticks_nodes(if val_on_right { (-y_tick_eff, 0.0) } else { (plot_w, plot_w + y_tick_eff) })?;
        let ghost_x = if val_on_right { -(y_tick_eff + y_label_gap) + 0.5 } else { plot_w + y_tick_eff + y_label_gap + 0.5 };
        let ghost_texts = ghost.as_ref().map_or(Ok(Vec::new()), |g| g.texts(&y_ticks, &y_labels))?;
        for (&tv, lbl) in y_ticks.iter().zip(ghost_texts.iter()) {
            ghost_items.push(y_label(ghost_x, (ghost_align, ghost_baseline), tv, lbl)?);
        }
        plot.extend(crate::contract::axis_group(
            crate::contract::AxisChannel::Y,
            &y_caption.untitled(),
            (if val_on_right { 0.5 } else { plot_w + 0.5 }, 0.0),
            ghost_items,
        )?);
    }

    body.push(Node::group(
        Attrs::new().set("transform", format!("translate({group_x:.3},{group_y:.3})")),
        plot,
    ));
    let canvas = Rect { x: 0.0, y: 0.0, width: frame.view_w, height: frame.h };
    let data_box = Rect { x: group_x, y: group_y, width: plot_w, height: plot_h };
    Ok(Layout {
        insets: canvas.insets_of(&data_box),
        data_box,
        nodes: vec![frame.svg(body)],
        diagnostics,
        title_pos: m.title_pos,
    })
}
