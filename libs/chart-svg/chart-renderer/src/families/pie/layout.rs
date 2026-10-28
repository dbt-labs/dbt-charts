//! Pie layout: every pixel decision. The standalone wheel lays out through `vega_fit` (label
//! ink pushes the plot margins, as `autosize: fit` does); the attached-table composition keeps
//! its own direct layout, mirroring production's Python SVG composer. A pie has no height of its
//! own and hugs nothing; only an attached table makes the canvas differ from the cell.

use d3rs::arc::Arc;
use d3rs::pie::pie;
use measure::{baseline_shift, Measure};
use scene::{Attrs, Chars, Escape, Extent, Node, TextBlock};

use super::bind::{Align, Bound, LegendPlan, Placement, SliceLabel, Wheel};
use super::Resolved;
use crate::common::{Font, TitleStyle};
use crate::contract::{self, ItemRole, Reads};
use crate::diagnostic::Diagnostic;
use crate::families::legend::{
    drawn_symbol_legend, legend_fit_bounds, legend_from_widths, legend_origin, legend_slide, limit_legend_text,
    LegendEntry, LegendSymbol, VegaLegend,
};
use crate::frame::{ChartFrame, FrameTitle};
use crate::layout::{IntrinsicWidth, Layout, Rect};
use crate::svg::{num, num_full, py_float_str};
use crate::vega_fit;

/// A pie fills the width it is given: nothing to hug.
pub fn intrinsic(_bound: &Bound, _measure: &Measure) -> Result<Option<IntrinsicWidth>, Diagnostic> {
    Ok(None)
}

/// A wheel draws in the slot sizing hands it, so that slot's height is its natural height at
/// any width. An attached table composes its own canvas (the wheel at the theme's view height,
/// the table, the gap), so its height is read back from the layout it draws.
pub fn height_for_width(bound: &Bound, width: f64, measure: &Measure) -> Result<f64, Diagnostic> {
    if bound.attached.is_none() {
        return Ok(bound.payload.height);
    }
    let cell = Rect { x: 0.0, y: 0.0, width, height: bound.payload.height };
    let laid_out = layout(bound, cell, measure)?;
    let [Node::Svg { extent, .. }] = laid_out.nodes.as_slice() else {
        return Err("pie: layout is one root card".to_string().into());
    };
    Ok(extent.height)
}

/// One pie in `cell`. An attached table makes the canvas taller or shorter than the cell: the
/// wheel renders at the theme's view height and the canvas takes its size from its children.
pub fn layout(bound: &Bound, cell: Rect, measure: &Measure) -> Result<Layout, Diagnostic> {
    let (composed, diagnostics) = crate::diagnostic::collect(|| compose(bound, cell, measure));
    // A layout that fails has no `Layout` to carry what it found: those WARNs reach the sink.
    let composed = composed.inspect_err(|_| diagnostics.iter().cloned().for_each(crate::diagnostic::warn))?;
    Ok(Layout {
        insets: composed.canvas.insets_of(&composed.data_box),
        data_box: composed.data_box,
        nodes: vec![composed.root],
        diagnostics,
        title_pos: composed.title_pos,
    })
}

struct Composed {
    root: Node,
    canvas: Rect,
    data_box: Rect,
    title_pos: Option<(f64, f64)>,
}

fn compose(bound: &Bound, cell: Rect, measure: &Measure) -> Result<Composed, Diagnostic> {
    let (r, payload) = (&bound.r, bound.payload);
    // The chart's OWN effective background — see `Resolved::background`.
    let frame = ChartFrame::at(cell.width, cell.height, payload.padding.as_ref(), r.background.as_str());
    let Some(attached) = &bound.attached else {
        let drawn = wheel(&frame, r, r.title_style.as_ref(), &bound.wheel, &payload.rows, &bound.thetas, bound.total, measure, 0.0)?;
        let body = if bound.faded {
            // `render_vega_spec`'s placeholder arm: the whole drawing faded under the overlay,
            // in the board's font.
            let style = payload
                .board_style
                .as_ref()
                .and_then(|b| b.placeholder.as_ref())
                .ok_or("pie: a placeholder chart needs board_style.placeholder")?;
            let family = style
                .overlay
                .font
                .family
                .as_deref()
                .ok_or("pie: a placeholder chart needs board_style.placeholder.overlay.font.family")?;
            let (text, weight) = style.overlay_label(None)?;
            vec![
                Node::group(Attrs::new().set("opacity", py_float_str(style.opacity)), drawn.nodes),
                style.overlay_node(frame.w, frame.h, family, text, weight),
            ]
        } else {
            drawn.nodes
        };
        return Ok(Composed {
            root: frame.root_node(body),
            canvas: Rect { x: 0.0, y: 0.0, width: frame.w, height: frame.h },
            data_box: drawn.plot,
            title_pos: drawn.title_pos,
        });
    };

    // The wheel and table are two independently-backgrounded children placed side by side or
    // stacked: the outer canvas itself carries no background fill, same as production leaves
    // the gap between them unpainted.
    let (w, (pad_l, pad_r, pad_t, pad_b)) = (frame.w, frame.pad);
    let gap = attached.gap;
    let table_laid = crate::families::table::layout::lay_out(&attached.table, attached.table_width, None, measure)?;
    table_laid.diagnostics.into_iter().for_each(crate::diagnostic::warn);
    let mut table_h = match table_laid.nodes.as_slice() {
        [Node::Svg { extent, .. }] => extent.height,
        _ => return Err("pie: the attached table lays out one root card".to_string().into()),
    };
    let mut table_nodes = table_laid.nodes;

    if let Some(heading) = &attached.heading {
        let block = heading.size + 2.0 + heading.gap;
        let text = Node::text(
            Attrs::new()
                .set("x", "0")
                .set("y", format!("{:.2}", heading.size * 0.8))
                .set("font-family", heading.family.as_str())
                .set("font-size", format!("{}", heading.size))
                .set("font-weight", heading.weight.as_str())
                .set("fill", heading.color.as_str()),
            heading.text.as_str(),
        );
        let table = Node::group(Attrs::new().set("transform", format!("translate(0, {block:.2})")), table_nodes);
        table_h += block;
        table_nodes = vec![Node::Svg {
            extent: Extent { width: attached.table_width, height: table_h },
            attrs: Attrs::new()
                .set("xmlns", "http://www.w3.org/2000/svg")
                .set("width", num(attached.table_width))
                .set("height", num(table_h)),
            children: vec![text, table],
        }];
    }

    // The wheel is a full standalone VL render, so the slot height plays no part: the composed
    // canvas takes its own outer size from the children, `donut_h + gap + table_h` below,
    // `max(donut_h, table_y + table_h)` at a side, the wheel's own height at the top. The canvas
    // legitimately grows past the slot or shrinks under it (a documented carve-out in the
    // geometry contract).
    let base_h = attached.view_height;

    // `placement == "top"` (vega_lite.py:605-628): the table sits between the title and the
    // plot, so the wheel's spec gains the table's height (heading included) plus the gap, added
    // to the view height because `autosize: fit` holds the whole view to it, and to the plot's
    // top through `shift_artifact_title` under a title (the same `shift_chart_title` law a row
    // shift runs) or `bump_padding_top` without one. The table lands where the plot would have
    // started: below the card padding and the title block.
    let titled = r.title.as_deref().is_some_and(|t| !t.is_empty());
    let (wheel_h, wheel_pad_t, pushed_title_style, top_table_y) = if attached.placement == Placement::Top {
        let room = table_h + gap;
        if titled {
            let shift = crate::TitleShift { title_dy: 0.0, body_dy: room };
            let shifted = crate::title_shift::shift_vega_payload(payload, shift)?;
            let title_style: Option<TitleStyle> = serde_json::from_value(shifted.resolved["title_style"].clone())
                .map_err(|e| format!("pie payload: {e}"))?;
            let pushed_pad_t = shifted
                .padding
                .expect("shift_vega_payload refuses a payload without padding")
                .top;
            (base_h + room, pushed_pad_t, title_style, pad_t + vega_title_block_height(r)?)
        } else {
            (base_h + room, pad_t + room, r.title_style.clone(), pad_t)
        }
    } else {
        (base_h, pad_t, r.title_style.clone(), 0.0)
    };
    // A left table pushes the wheel right by its own width and gap.
    let title_dx = if attached.placement == Placement::Left { -(attached.table_width + gap) } else { 0.0 };

    let wheel_frame = ChartFrame::new(
        attached.wheel_width,
        wheel_h,
        (pad_l, pad_r, wheel_pad_t, pad_b),
        r.background.as_str(),
        num_full,
    );
    let drawn = wheel(
        &wheel_frame,
        r,
        pushed_title_style.as_ref(),
        &bound.wheel,
        &payload.rows,
        &bound.thetas,
        bound.total,
        measure,
        title_dx,
    )?;

    let (outer_h, donut_x, table_x, table_y) = match attached.placement {
        Placement::Left | Placement::Right => {
            // The table aligns down the wheel's disk, not the card, and never above the disk's top.
            let diameter = 2.0 * drawn.radius;
            let disk_top = ((wheel_h - diameter) / 2.0).max(0.0);
            let table_y = disk_top.max(match attached.align {
                Align::Start => disk_top,
                Align::Center => disk_top + (diameter - table_h) / 2.0,
                Align::End => disk_top + diameter - table_h,
            });
            let (donut_x, table_x) = if attached.placement == Placement::Right {
                (0.0, attached.wheel_width + gap)
            } else {
                (attached.table_width + gap, pad_l)
            };
            (wheel_h.max(table_y + table_h), donut_x, table_x, table_y)
        }
        Placement::Top | Placement::Bottom => {
            // Across the card inside its padding.
            let across_x = match attached.align {
                Align::Start => pad_l,
                Align::Center => (w - attached.table_width) / 2.0,
                Align::End => w - attached.table_width - pad_r,
            };
            let donut_x = (w - attached.wheel_width) / 2.0;
            if attached.placement == Placement::Top {
                (wheel_h, donut_x, across_x, top_table_y)
            } else {
                (wheel_h + gap + table_h, donut_x, across_x, wheel_h + gap)
            }
        }
    };

    let donut = Node::group(Attrs::new().set("transform", format!("translate({donut_x:.2}, 0)")), drawn.nodes);
    let table = Node::group(
        Attrs::new().set("transform", format!("translate({table_x:.2}, {table_y:.2})")),
        table_nodes,
    );
    Ok(Composed {
        root: frame.sized(w, outer_h).root_node(vec![donut, table]),
        canvas: Rect { x: 0.0, y: 0.0, width: w, height: outer_h },
        data_box: Rect { x: drawn.plot.x + donut_x, ..drawn.plot },
        title_pos: drawn.title_pos,
    })
}

/// `title_band.py::vega_title_block_height`: a titled chart's title block down to the plot,
/// padding excluded — the title font size, one subtitle line (its font size plus Vega's default
/// 3px subtitle padding) and `title.offset` (Vega's own 4 when unset).
fn vega_title_block_height(r: &Resolved) -> Result<f64, String> {
    let title_size = r
        .style
        .title_font
        .as_ref()
        .and_then(|f| f.size)
        .ok_or("pie: a titled attached_table at the top requires style.title_font.size")?;
    let subtitle = if r.subtitle.as_deref().is_some_and(|s| !s.is_empty()) {
        let size = r
            .title_style
            .as_ref()
            .and_then(|ts| ts.subtitle.as_ref())
            .and_then(|sub| sub.font.size)
            .ok_or("pie: a subtitled attached_table at the top requires title_style.subtitle.font.size")?;
        size + crate::constants::layout::VEGA_SUBTITLE_PADDING
    } else {
        0.0
    };
    // The shared shift law (`title_shift::shift_vega_payload`) has already refused an unset
    // offset before this runs; this read never falls back.
    let offset = r
        .title_style
        .as_ref()
        .and_then(|ts| ts.position.as_ref())
        .and_then(|pos| pos.offset)
        .ok_or("pie: a titled attached_table at the top requires title_style.position.offset")?;
    Ok(title_size + subtitle + offset)
}

/// A pie's color legend as Vega lays it out: `(label, fill)` per entry plus the shared grid
/// layout, and the slice stroke color the symbols carry.
struct PieLegend {
    entries: Vec<(String, String)>,
    keys: Vec<Option<String>>,
    layout: VegaLegend,
    symbol_stroke: Option<String>,
}

impl PieLegend {
    fn entries(&self) -> Vec<LegendEntry<'_>> {
        self.entries
            .iter()
            .map(|(label, fill)| LegendEntry { label, fill, symbol: None, series_key: None, recede: None })
            .collect()
    }

    /// A filled `arc` mark's legend symbol: Vega's default `circle` in the scale's fill,
    /// stroked in the mark's own constant `stroke` (`legend/encode.ts` `symbols`: the mark
    /// `stroke` flows, its width and join do not), unstroked when the mark has none.
    fn symbol(&self) -> LegendSymbol<'_> {
        match &self.symbol_stroke {
            Some(stroke) => LegendSymbol::StrokedCircle(stroke),
            None => LegendSymbol::FilledCircle,
        }
    }
}

/// The bound legend's text limited to its label and title widths, then laid out through the
/// shared Vega layout.
fn lay_out_legend(plan: &LegendPlan, r: &Resolved, measure: &Measure) -> Result<PieLegend, String> {
    let cfg = &r.legend;
    let mut entries = plan.entries.clone();
    let limited = limit_legend_text(entries.iter().map(|(label, _)| label.as_str()), &plan.title, cfg, measure.fonts)?;
    for ((label, _), drawn) in entries.iter_mut().zip(limited.labels) {
        *label = drawn;
    }
    let (label_ws, title, title_w) = (limited.label_ws, limited.title, limited.title_w);
    // An unstroked symbol bounds the bare 10px glyph (no stroke ring), which tightens the row
    // pitch; a stroked one is the grid's default entry.
    let entry_symbol = plan.symbol_stroke.is_none().then_some(LegendSymbol::FilledCircle);
    let layout = legend_from_widths(&label_ws, &vec![entry_symbol; entries.len()], false, &title, title_w, cfg)?;
    Ok(PieLegend { entries, keys: plan.keys.clone(), layout, symbol_stroke: plan.symbol_stroke.clone() })
}

struct DrawnWheel {
    nodes: Vec<Node>,
    radius: f64,
    plot: Rect,
    title_pos: Option<(f64, f64)>,
}

/// A slice label placed: the angle it sits at and the widest of its lines.
struct Placed<'a> {
    mid: f64,
    max_w: f64,
    label: &'a SliceLabel,
}

/// The text attributes a font carries, up to the weight; the caller adds the fill.
fn text_font(attrs: Attrs, font: &Font, size: f64, default_family: &str) -> Attrs {
    let attrs = attrs.vega("font-family", font.family_or(default_family)).set("font-size", format!("{size}"));
    let attrs = match font.style.as_deref() {
        Some(style) => attrs.set("font-style", style),
        None => attrs,
    };
    attrs.set("font-weight", font.weight_str())
}

/// Standalone pie through Vega's own fit-margin algebra: production compiles the wheel as a
/// layer spec under `autosize: fit` where the slice-label text ink pushes the PLOT MARGINS
/// (asymmetric left/right — donut_example's origin is (33, 44)) while the radius stays
/// `min(pw, ph)/2 × outer_fraction` of the fitted box, re-evaluated per resize pass.
/// `fit_plot`'s passes model that directly: each candidate (pw, ph) re-derives the radius and
/// label anchors and unions their ink. A wheel's label ink moves WITH the plot, so the sequence
/// generally has no fixed point — which is why `fit_plot` runs Vega's own bounded pass count
/// rather than iterating to one (`AUTOSIZE_PADDING_PASSES`). The attached-table composition
/// renders its wheel through this same fit path at `width=wheel_width` and the theme's unsized
/// view height; only the outer wheel+table placement is its own arithmetic.
#[allow(clippy::too_many_arguments)]
fn wheel(
    frame: &ChartFrame,
    r: &Resolved,
    title_style: Option<&TitleStyle>,
    wheel: &Wheel,
    rows: &[serde_json::Map<String, serde_json::Value>],
    thetas: &[f64],
    total: f64,
    measure: &Measure,
    title_dx: f64,
) -> Result<DrawnWheel, Diagnostic> {
    let fonts = measure.fonts;
    let (w, h) = (frame.w, frame.h);
    let (pad_l, pad_r, pad_t, pad_b) = frame.pad;

    let legend = wheel.legend.as_ref().map(|plan| lay_out_legend(plan, r, measure)).transpose()?;
    let labels_cfg = r.style.slice_mark.labels.as_ref();
    // Per-visible-slice label geometry, fixed ahead of the fit (only the anchor positions vary
    // with the candidate plot box).
    let mut placed: Vec<Placed> = Vec::new();
    if let Some(labels) = labels_cfg {
        let font_size = labels.font.size_or(14.0);
        let family = labels.font.family_or("sans-serif");
        let mut mids = Vec::with_capacity(thetas.len());
        let mut running = 0.0;
        for theta in thetas {
            mids.push(std::f64::consts::TAU * (running + theta / 2.0) / total);
            running += theta;
        }
        for label in &wheel.labels {
            // The label's right (or left) edge joins the autosize fit, so it is a width Vega
            // measures: kerning included.
            let max_w = measure
                .shaped_widths(&label.lines, family, font_size)?
                .into_iter()
                .fold(0.0_f64, f64::max);
            placed.push(Placed { mid: mids[label.idx], max_w, label });
        }
    }
    let label_fs = labels_cfg.map(|l| l.font.size_or(14.0)).unwrap_or(14.0);
    let label_lh = labels_cfg.map(|l| l.line_height).unwrap_or(14.0);
    let label_offset = labels_cfg.map(|l| l.offset).unwrap_or(6.0);

    // Title ink (width-resolved font slot). Overflowing text is processed exactly as
    // production's `apply_title_overflow_to_spec` processes it BEFORE the wheel render — the
    // shared `wrap_title_block` law.
    let title = FrameTitle::new(
        r.title.as_deref(),
        r.subtitle.as_deref(),
        r.style.title_font.as_ref(),
        title_style,
        frame.avail_w(),
        fonts,
    )?;
    title.warn_truncated(&r.id);

    let arc_stroke_w = r.style.slice_mark.stroke.as_ref().and_then(|s| s.width).unwrap_or(0.0);

    // Center-total text marks are real VL marks — their ink joins the fit union (a long total
    // can outgrow the wheel).
    let total_ink: Option<(f64, f64, Option<(f64, f64)>)> = match (&r.total, &wheel.center) {
        (Some(total_cfg), Some(text)) => {
            let value_font = &r.style.total_style.value.font;
            let value_size = value_font.size_or(14.0);
            let value_w = fonts.measure(text, value_font.family_or("sans-serif"), value_size)?;
            let label_dims = match &total_cfg.label {
                Some(label_text) => {
                    let label_font = &r.style.total_style.label.font;
                    let label_size = label_font.size_or(11.0);
                    let lw = fonts.measure(label_text, label_font.family_or("sans-serif"), label_size)?;
                    Some((lw, label_size))
                }
                None => None,
            };
            Some((value_w, value_size, label_dims))
        }
        _ => None,
    };

    // The legend's (x, y) in plot coordinates, recorded by the fit's last (drawn) layout pass.
    let mut legend_xy = (0.0, 0.0);
    let m = vega_fit::fit_plot(
        w,
        h,
        (pad_l, pad_r, pad_t, pad_b),
        |pw, ph, b| {
            let radius = pw.min(ph) / 2.0 * r.outer_fraction;
            let (cx, cy) = (pw / 2.0, ph / 2.0);
            // Wheel ink: arc marks expand by their stroke (boundStroke).
            b.union(&vega_fit::Bounds::new(cx - radius, cy - radius, cx + radius, cy + radius).expand(arc_stroke_w));
            if let Some((value_w, value_fs, label_dims)) = &total_ink {
                b.union(&vega_fit::text_ink(cx, cy, *value_w, *value_fs, "center", "bottom"));
                if let Some((label_w, label_fs)) = label_dims {
                    b.union(&vega_fit::text_ink(cx, cy, *label_w, *label_fs, "center", "top"));
                }
            }
            for sl in &placed {
                let top = sl.mid.cos() >= 0.0;
                let right = sl.mid.sin() >= 0.0;
                let x = cx + sl.mid.sin() * (radius + label_offset);
                let y = cy - sl.mid.cos() * (radius + label_offset)
                    + if top { -(sl.label.lines.len() as f64 * label_lh) } else { 0.0 };
                let mut ti = vega_fit::text_ink(x, y, sl.max_w, label_fs, if right { "left" } else { "right" }, "top");
                if sl.label.lines.len() > 1 {
                    ti.y2 += (sl.label.lines.len() as f64 - 1.0) * label_lh;
                }
                b.union(&ti);
            }
            // The legend group's own bounds join the view ink on its own axis only:
            // `ViewLayout.js` unions a left/right legend as `F.add(o.x1, 0).add(o.x2, 0)` and a
            // top/bottom one as `F.add(0, o.y1).add(0, o.y2)` under autosize fit. A pie has no
            // axes, so the legend anchors on the plot rect alone: `ceil(x2)` or `ceil(y2)` plus
            // the layout offset past the near edge, `floor(x1)`/`floor(y1)` (0) minus offset
            // minus its own extent before the far one.
            if let Some(lg) = &legend {
                let (box_w, box_h) = (lg.layout.box_w, lg.layout.box_h());
                // A wheel has no axes and its arcs are bare marks: the bounds are the plot
                // rect's own.
                legend_xy = legend_origin(&r.legend.position, (0.0, pw), (0.0, ph), (pw, ph), (box_w, box_h));
                if let Some(reach) = legend_fit_bounds(&r.legend.position, legend_xy, (box_w, box_h)) {
                    b.union(&reach);
                }
            }
        },
        title.ink(),
    );
    if m.plot_w <= 0.0 || m.plot_h <= 0.0 {
        return Err(format!("pie: plot box too small ({}×{})", m.plot_w, m.plot_h).into());
    }
    let (pw, ph) = (m.plot_w, m.plot_h);
    let radius = pw.min(ph) / 2.0 * r.outer_fraction;
    let inner = radius * r.style.inner_radius;
    let (cx, cy) = (pad_l + m.left + pw / 2.0, pad_t + m.top + ph / 2.0);
    if let (Some(text), Some((value_w, _, _))) = (&wheel.center, &total_ink) {
        if inner > 0.0 && *value_w > 2.0 * inner {
            crate::diagnostic::warn(crate::diagnostic::Diagnostic::new(
                &crate::codes::WARN_PIE_TOTAL_EXCEEDS_INNER_RADIUS,
                [
                    ("chart_id", serde_json::json!(r.id)),
                    ("formatted_value", serde_json::json!(text)),
                    ("hole_diameter", serde_json::json!(2.0 * inner)),
                    ("slot_height", serde_json::json!(ph)),
                    ("slot_width", serde_json::json!(pw)),
                    ("text_width", serde_json::json!(value_w)),
                ],
            ));
        }
    }

    let mut nodes = vec![frame.background_node()];
    if m.title_pos.is_some() && !title.block().title_lines.is_empty() {
        let title_nodes = title.nodes(m.title_pos, frame.origin(&m));
        // A left-placed table pushes the wheel right, and the title group slides back by that
        // much so it stays on the card's own left edge. Printed `{dx:g}`.
        if title_dx != 0.0 {
            let transform = format!("translate({},0)", d3rs::py_float::format_g(title_dx));
            nodes.push(Node::group(Attrs::new().set("transform", transform), title_nodes));
        } else {
            nodes.extend(title_nodes);
        }
    }

    let gap = r.style.slice_mark.gap.unwrap_or(0.0);
    let corner = r.style.slice_mark.corner_radius.unwrap_or(0.0);
    let slices = pie(thetas, 0.0, std::f64::consts::TAU, gap);
    let mark_style = |anchor: Option<crate::href::Anchor>| {
        let mut attrs = Attrs::new();
        if let Some(s) = &r.style.slice_mark.stroke {
            attrs = attrs
                .set("stroke", crate::href::Anchor::paint(anchor, s.color.as_deref().unwrap_or("none")))
                .set("stroke-width", format!("{}", s.width.unwrap_or(1.0)))
                .set("stroke-linejoin", s.join.as_deref().unwrap_or("miter"));
        }
        match r.style.slice_mark.opacity {
            Some(opacity) => attrs.set("opacity", format!("{opacity}")),
            None => attrs,
        }
    };
    let reads_of = |idx: usize| match wheel.slices[idx].aria.as_deref() {
        Some(label) => Reads::Label(label),
        None => Reads::Bare,
    };
    let mut wedges = Vec::with_capacity(slices.len());
    for slice in &slices {
        let path = Arc {
            inner_radius: inner,
            outer_radius: radius,
            corner_radius: corner,
            pad_radius: None,
            start_angle: slice.start_angle,
            end_angle: slice.end_angle,
            pad_angle: slice.pad_angle,
        }
        .path();
        let bound_slice = &wheel.slices[slice.index];
        let anchor = r.selection.mark(r.link_open.anchor(bound_slice.href.as_deref()));
        let fill = crate::href::Anchor::paint(anchor, &bound_slice.fill);
        wedges.push(contract::item_group("arc", "layer_0_marks", ItemRole::ArcMark, reads_of(slice.index), |aria| {
            let attrs = Attrs::new()
                .set("d", path)
                .set("fill", fill)
                .extend(mark_style(anchor))
                .extend(aria);
            crate::href::linked(anchor, Node::Path(attrs))
        })?);
    }
    nodes.push(Node::group(Attrs::new().set("transform", format!("translate({cx},{cy})")), wedges));

    // Center total — Vega text baselines: value "bottom", caption "top". Both text layers carry
    // one row, so a description inherited from the arc layer would name the FIRST slice
    // whichever slice is meant: each is a hidden mark set.
    if let (Some(total_cfg), Some(text)) = (&r.total, &wheel.center) {
        let center_href = super::bind::center_href(r, rows)?;
        let anchor = r.link_open.anchor(center_href.as_deref());
        let value_font = &r.style.total_style.value.font;
        let value_size = value_font.size_or(14.0);
        let value_y = cy + baseline_shift("bottom", value_size);
        let value_text = Node::text(
            text_font(
                Attrs::new()
                    .set("x", format!("{cx:.2}"))
                    .set("y", format!("{value_y:.2}"))
                    .set("text-anchor", "middle"),
                value_font,
                value_size,
                "sans-serif",
            )
            .set("fill", value_font.color.as_deref().unwrap_or("#000")),
            text.as_str(),
        );
        nodes.push(contract::hidden_mark_group("text", "layer_1_marks", vec![crate::href::linked(anchor, value_text)]));
        if let Some(label_text) = &total_cfg.label {
            let label_font = &r.style.total_style.label.font;
            let label_size = label_font.size_or(11.0);
            let label_y = cy + baseline_shift("top", label_size);
            let label_node = Node::text(
                text_font(
                    Attrs::new()
                        .set("x", format!("{cx:.2}"))
                        .set("y", format!("{label_y:.2}"))
                        .set("text-anchor", "middle"),
                    label_font,
                    label_size,
                    "sans-serif",
                )
                .set("fill", label_font.color.as_deref().unwrap_or("#000")),
                label_text.as_str(),
            );
            nodes.push(contract::hidden_mark_group("text", "layer_2_marks", vec![crate::href::linked(anchor, label_node)]));
        }
    }

    // Slice labels at the fitted geometry — a real per-row data-bound layer (unlike the
    // center-total texts above), so each label's own aria-label is its OWN row's, never row 0's.
    if let Some(labels) = labels_cfg {
        for sl in &placed {
            let top = sl.mid.cos() >= 0.0;
            let right = sl.mid.sin() >= 0.0;
            let x = num_full(cx + sl.mid.sin() * (radius + label_offset));
            let y_anchored = cy - sl.mid.cos() * (radius + label_offset)
                + if top { -(sl.label.lines.len() as f64 * label_lh) } else { 0.0 };
            let first_dy = baseline_shift("top", label_fs);
            let anchor = r.selection.mark(r.link_open.anchor(wheel.slices[sl.label.idx].href.as_deref()));
            let tspans = sl
                .label
                .lines
                .iter()
                .enumerate()
                .map(|(i, line)| {
                    Node::Tspan(TextBlock {
                        attrs: Attrs::new()
                            .set("x", x.as_str())
                            .set("dy", format!("{}", if i == 0 { 0.0 } else { label_lh })),
                        text: Chars::new(line.as_str(), Escape::Attr),
                    })
                })
                .collect::<Vec<_>>();
            nodes.push(contract::item_group(
                "text",
                "layer_3_marks",
                ItemRole::TextMark,
                reads_of(sl.label.idx),
                |aria| {
                    let attrs = text_font(
                        Attrs::new()
                            .set("x", x.as_str())
                            .set("y", num_full(y_anchored + first_dy))
                            .set("text-anchor", if right { "start" } else { "end" }),
                        &labels.font,
                        label_fs,
                        "sans-serif",
                    )
                    .set("fill", crate::href::Anchor::paint(anchor, &sl.label.fill))
                    .extend(aria);
                    crate::href::linked(anchor, Node::TextRuns { attrs, children: tspans })
                },
            )?);
        }
    }

    // The legend group paints after every mark layer, slid along its strip for `align`
    // center/end (`legend_align.py`), against the plot rect the scenegraph reports rather than
    // the drawn one.
    if let Some(lg) = &legend {
        let (dx, dy) = legend_slide(&r.id, &r.legend.position, &lg.layout, m.scenegraph_plot)?;
        let keys: Vec<Option<&str>> = lg.keys.iter().map(Option::as_deref).collect();
        nodes.extend(drawn_symbol_legend(
            &r.selection.legend(lg.entries(), &keys),
            &lg.layout,
            &r.legend,
            lg.symbol(),
            pad_l + m.left + legend_xy.0 + dx,
            pad_t + m.top + legend_xy.1 + dy,
            false,
        ));
    }

    Ok(DrawnWheel {
        nodes,
        radius,
        plot: Rect { x: pad_l + m.left, y: pad_t + m.top, width: pw, height: ph },
        title_pos: m.title_pos,
    })
}
