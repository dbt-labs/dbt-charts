//! Heatmap layout: every pixel decision. Vega's `autosize: fit` negotiation (`vega_fit::fit_plot`)
//! runs over the plot rect, both axes' label ink and the right gradient legend's box; the cell
//! colors and the legend's labeled ticks come off ONE production tick ladder
//! (`emitters/_channels.py`).
//!
//! Unsupported → clear Err:
//! - a vertical gradient legend above or below the plot, or a right-hand y
//!   axis — neither has a probed geometry, and no corpus board draws one

use scene::{Attrs, Node};

use super::bind::{Bound, Coloring};
use super::{
    cell_nodes, category_legend_entries, date_gradient_def, date_legend_values, date_scale, dated_bands, flat_label,
    legend_date_label, rotated_label, svg_text_anchor, tick_line, y_labels_own_side, GradientColors, LabelFace,
    AXIS_TRANSLATE, BREATHING_ROOM_PX,
};
use crate::diagnostic::Diagnostic;
use crate::families::axis_caption::category_axis_caption;
use crate::families::cartesian::BandScale;
use crate::families::color_scale::{GradientAt, VerticalGradient};
use crate::families::heatmap_facet::{AxisLabel, GradientLegend, GradientPaint, HeatmapXAxis};
use crate::families::legend::{
    gradient_legend_box, gradient_length, horizontal_gradient_length, legend_fit_bounds, legend_origin, legend_slide,
    slide_block, drawn_symbol_legend, vega_legend, LegendEntry, LegendSymbol, VEGA_GRADIENT_LENGTH_MAX,
    VEGA_GRADIENT_LENGTH_MIN,
};
use crate::families::temporal::{
    axis_time_format, fold_steep, resolve_vertical_temporal_labels, rewrite_with_expr, AuthoredTimeFormat, ExprBands,
    TemporalLabel, TimeUnit,
};
use crate::frame::{ChartFrame, FrameTitle};
use crate::layout::{IntrinsicWidth, Layout, Rect};
use crate::vega_fit::{self, Bounds};
use crate::common::AxisStyle;
use measure::{FontBook, Measure};

/// A heatmap fills the width it is given: nothing to hug.
pub fn intrinsic(_bound: &Bound, _measure: &Measure) -> Result<Option<IntrinsicWidth>, Diagnostic> {
    Ok(None)
}

/// A heatmap draws in the slot sizing hands it: that slot's height is its natural height at
/// any width, and [`layout`] draws the cell it is given.
pub fn height_for_width(bound: &Bound, _width: f64, _measure: &Measure) -> Result<f64, Diagnostic> {
    Ok(bound.height)
}

/// One drawn edge of the y category axis: whether it sits on the right, and
/// how its labels anchor.
struct YEdge<'a> {
    right: bool,
    align: &'static str,
    baseline: &'static str,
    /// The mirror ghost's own label text and width per category, when its format relabels them.
    relabeled: Option<(&'a [String], Vec<f64>)>,
}

/// The calendar labels a date-valued y band axis paints, or `None` for a
/// category y (also a date column with no detectable calendar grain, which
/// production leaves as raw band values), and the `labelSeparation` of the
/// Vega greedy overlap pass the leftovers go through when even the coarsest
/// cadence collides. Refuses by name every date-y setting the ladder does
/// not model.
#[allow(clippy::too_many_arguments)]
fn y_date_labels(
    domain: &[String],
    grain: Option<TimeUnit>,
    ax_y: &AxisStyle,
    chart_h: f64,
    usable_ratio: Option<f64>,
    fonts: &FontBook,
    family: &str,
    font_size: f64,
) -> Result<(Option<Vec<AxisLabel>>, Option<f64>), String> {
    let Some(data_tu) = grain else {
        return Ok((None, None));
    };
    let bands = dated_bands(domain);
    let dated: Vec<String> = bands.iter().map(|&i| domain[i].clone()).collect();
    let labels_cfg = &ax_y.labels;
    let authored = AuthoredTimeFormat::parse(
        "heatmap",
        axis_time_format(labels_cfg.spec(), &ax_y.grain_formats, Some(data_tu)),
    )?;
    let usable_ratio = usable_ratio.ok_or("heatmap: label_usable_ratio absent (theme required for the y axis ladder)")?;
    let overlap = labels_cfg.overlap.as_ref().ok_or("heatmap: axis_y.labels.overlap absent (theme required)")?;
    let min_gap = labels_cfg.min_gap.ok_or("heatmap: axis_y.labels.min_gap absent (theme required)")?;
    let ladder = resolve_vertical_temporal_labels(
        &dated,
        data_tu,
        chart_h * usable_ratio,
        ax_y.fiscal_year_start_month,
        overlap.skip,
        font_size,
        min_gap,
        authored,
    )?;
    let greedy_gap = (!ladder.fits && overlap.skip).then_some(min_gap);
    let labels = ladder.labels.into_iter().map(|l| TemporalLabel { domain_idx: bands[l.domain_idx], ..l }).collect::<Vec<_>>();
    let labels = match labels_cfg.expr.as_deref() {
        Some(expr) => {
            let expr = crate::labels_expr::Expr::parse(expr).map(|e| e.on("y")).map_err(|e| format!("heatmap: {e:?}"))?;
            rewrite_with_expr(labels, domain, &expr, ExprBands::KeptByLadder).map_err(|e| format!("heatmap: {e}"))?
        }
        None => fold_steep(labels, labels_cfg.angle, data_tu),
    };
    let labels = labels_cfg.cased_labels(labels)?;
    let labels = labels
        .into_iter()
        .map(|l| AxisLabel::measured(l.domain_idx, l.row1, l.row2, &|t| fonts.measure(t, family, font_size)))
        .collect::<Result<Vec<_>, _>>()?;
    Ok((Some(labels), greedy_gap))
}

pub fn layout(bound: &Bound, cell: Rect, measure: &Measure) -> Result<Layout, Diagnostic> {
    let r = &bound.r;
    let fonts = measure.fonts;
    // The chart's OWN effective background -- see `HeatmapResolved::background`.
    let frame = ChartFrame::at_view(
        cell.width,
        cell.height,
        bound.padding.as_ref(),
        bound.board_style.as_ref(),
        r.background.as_str(),
        crate::ViewScale::Discrete,
    )?;
    let (w, h) = (frame.w, frame.h);
    let (x_domain, y_domain) = (&bound.x.domain, &bound.y.domain);
    let mut diagnostics = Vec::new();

    // ── X axis ────────────────────────────────────────────────────────────
    // A date x runs bar's bucketed-calendar ladder instead of the raw ordinal
    // band (`build_cartesian_x_encoding` keeps the band scale and borrows the
    // ladder's labels): the rows are already in date order (`date_dimension`),
    // so the band domain is the ladder's own order.
    let ax_x = &r.style.axis_x;
    let x_dates = if let Some(data_tu) = bound.x.grain {
        let usable_ratio = r
            .style
            .label_usable_ratio
            .ok_or("heatmap: label_usable_ratio absent (theme required for the x axis ladder)")?;
        Some(HeatmapXAxis::dates(&bound.x.first_seen, data_tu, ax_x, w, usable_ratio, fonts)?)
    } else {
        None
    };
    let x_tick_visible = ax_x.ticks.visible.unwrap_or(true);
    let x_tick_len = if x_tick_visible { ax_x.ticks.length.unwrap_or(6.0) } else { 0.0 };
    let x_axis_offset = ax_x.ticks.offset.unwrap_or(0.0);
    let x_label_pad = ax_x
        .labels
        .padding
        .ok_or("heatmap: axis_x.labels.padding absent (theme required)")?;
    let x_label_fs = ax_x.labels.font.size_or(11.0);
    let x_label_family = ax_x.labels.font.family_or("sans-serif");
    // Heatmap's x is always an ordinal band (`resolve_cartesian_x_type`'s
    // own heatmap branch pins it), so it runs bar's ordinal overlap ladder
    // over the slot width -- production's `resolve_axis_x_overlap` with
    // `chart_width=box.width`.
    let x_angle = vega_fit::normalize_angle(match ax_x.labels.angle {
        _ if x_dates.is_some() => 0.0,
        Some(angle) => angle,
        None => vega_fit::category_label_layout(
            &ax_x.labels,
            x_domain,
            w,
            r.style.label_usable_ratio,
            fonts,
            x_label_fs,
        )?
        .angle,
    });
    let x_edge = ax_x.x_edge();
    let (x_align, x_baseline) = if x_angle == 0.0 {
        ("center", x_edge.baseline())
    } else {
        vega_fit::vl_x_label_anchor(x_angle, x_edge == crate::common::XEdge::Bottom)
    };
    let x_label_limit = ax_x.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    let x_texts = &bound.x.texts;
    let x_label_ws = measure.capped_widths(x_texts, x_label_family, x_label_fs, x_label_limit)?;

    // ── Y category axis ───────────────────────────────────────────────────
    let ax_y = &r.style.axis_y;
    let ghost = &bound.ghost;
    let mirror_active = ghost.is_some();
    let y_label_family = ax_y.labels.font.family_or("sans-serif");
    let y_label_fs = ax_y.labels.font.size_or(11.0);
    let y_tick_visible = ax_y.ticks.visible.unwrap_or(true);
    let y_tick_len = if y_tick_visible { ax_y.ticks.length.unwrap_or(6.0) } else { 0.0 };
    let y_label_limit = ax_y.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    let (y_dates, y_greedy_gap) = y_date_labels(
        &bound.y.first_seen,
        bound.y.grain,
        ax_y,
        h,
        r.style.label_usable_ratio,
        fonts,
        y_label_family,
        y_label_fs,
    )?;
    let y_label_bands: Vec<String> = match &y_dates {
        Some(labels) => labels.iter().map(|l| y_domain[l.domain_idx].clone()).collect(),
        None => y_domain.clone(),
    };
    let y_label_texts: &[String] = if y_dates.is_some() { &y_label_bands } else { &bound.y.texts };
    let y_label_max_w = measure.widest(y_label_texts, y_label_family, y_label_fs)?;
    let y_label_ws = match &y_dates {
        Some(labels) => labels.iter().map(|l| l.w1.max(l.w2).min(y_label_limit)).collect(),
        None => measure.capped_widths(y_label_texts, y_label_family, y_label_fs, y_label_limit)?,
    };
    // Each drawn label's width and whether it carries a second row; hidden
    // labels draw none.
    let y_label_specs: Vec<(f64, usize)> = if ax_y.labels.visible == Some(false) {
        Vec::new()
    } else {
        match &y_dates {
            Some(labels) => labels.iter().zip(&y_label_ws).map(|(l, &w)| (w, usize::from(!l.row2.is_empty()))).collect(),
            None => y_label_ws.iter().map(|&w| (w, 0)).collect(),
        }
    };
    // An own-side `labels.align` (`left` on a left axis, `right` on a right
    // one) grows the row labels back ACROSS the axis line, which Vega-Lite's
    // own fit estimate does not reserve for. Rather than leave them over the
    // plot, production measures the exact nominal row labels and bakes
    // `labelPadding = max_width + 4`, capped at the label limit
    // (`vl_field_maps.py::measure_axis_to_vl`); Vega then anchors every label
    // at -labelPadding. An align it cannot measure from, `center` or an
    // upper/lower `font.case` (which renders through a `labelExpr` the
    // measurement can't see), drops back to Vega-Lite's away-side default
    // (`_fallback_reversed_label_align`). A date y paints a `labelExpr` too,
    // so it always takes the away-side default.
    let y_right = ax_y.position.as_deref() == Some("right");
    let own_align = if y_right { "right" } else { "left" };
    let y_own_side = y_labels_own_side(ax_y, y_dates.is_some());
    let y_label_pad = if y_own_side {
        y_label_max_w.min(y_label_limit) + BREATHING_ROOM_PX
    } else {
        ax_y.labels
            .padding
            .ok_or("heatmap: axis_y.labels.padding absent (theme required)")?
    };
    // An authored align wins; else Vega-Lite's default for the angle (`right`, the
    // text ending at the axis, when flat).
    let y_angle = vega_fit::normalize_angle(ax_y.labels.angle.unwrap_or(0.0));
    let (vl_y_align, y_baseline) = vega_fit::vl_y_label_anchor(y_angle, !y_right);
    let y_ink_align = if y_own_side { own_align } else { vl_y_align };
    let (ghost_default_align, ghost_baseline) = vega_fit::vl_y_label_anchor(y_angle, y_right);
    let mut y_edges = vec![YEdge { right: y_right, align: y_ink_align, baseline: y_baseline, relabeled: None }];
    if ghost.is_some() {
        let align = if y_own_side { own_align } else { ghost_default_align };
        let relabeled = match &bound.ghost_texts {
            Some(texts) => {
                let ws = measure.capped_widths(texts, y_label_family, y_label_fs, y_label_limit)?;
                Some((texts.as_slice(), ws))
            }
            None => None,
        };
        y_edges.push(YEdge { right: !y_right, align, baseline: ghost_baseline, relabeled });
    }
    let y_labels_hidden = ax_y.labels.visible == Some(false);
    let y_ink = |edge: &YEdge, anchor_x: f64, cy: f64, label_w: f64, extra_rows: usize| {
        crate::common::rotated_category_label_ink(anchor_x, cy, label_w, y_label_fs, edge.align, edge.baseline, y_angle, extra_rows)
    };
    let y_gutter = y_tick_len + y_label_pad;
    // The labels' anchor x past an edge, for a plot `pw` wide.
    let y_anchor_x = |edge: &YEdge, pw: f64| if edge.right { pw + y_gutter } else { -y_gutter };

    // ── Axis titles ───────────────────────────────────────────────────────
    let (x_title_text, x_cut) =
        crate::common::axis_title_lines(&r.id, "x_label", &bound.x.title, &ax_x.title.font, w, 1.0, fonts)?;
    let (y_title_text, y_cut) =
        crate::common::axis_title_lines(&r.id, "y_label", &bound.y.title, &ax_y.title.font, h, 1.0, fonts)?;
    diagnostics.extend(x_cut);
    diagnostics.extend(y_cut);
    let x_dates = x_dates.map(|axis| axis.with_caption_title(&x_title_text));
    let x_title_w = crate::common::widest_title_line(&ax_x.title, &x_title_text, fonts)?;
    let y_title_w = crate::common::widest_title_line(&ax_y.title, &y_title_text, fonts)?;
    let x_title_extra = crate::common::x_title_extra(&ax_x.title, &x_title_text);
    let y_title_extra = crate::common::y_title_extra(&ax_y.title, &y_title_text);
    // Where the rotated title starts from: the row labels' outer edge, which
    // `axisLayout` reads off the axis bounds (`s = -bounds.x1`, `vega.js:20389`)
    // before `axisTitleLayout` (`vega.js:20422-20432`) sits the title `padding`
    // past it. Own-side labels start at the gutter and grow toward the plot;
    // away-side ones end there and grow left.
    // Hidden labels leave the axis its ticks, which the title clears.
    let y_tick_edge = if y_labels_hidden { y_tick_len } else { 0.0 };
    let y_label_edge = y_label_specs.iter().fold(if y_right { y_tick_edge } else { -y_tick_edge }, |edge, &(lw, extra_rows)| {
        let ink = y_ink(&y_edges[0], y_anchor_x(&y_edges[0], 0.0), 0.0, lw, extra_rows);
        if y_right { edge.max(ink.x2) } else { edge.min(ink.x1) }
    });
    // How far the x labels reach below their row's anchor: the bottom title
    // sits `padding` past it (`s = bounds.y2`, `vega.js:20403-20405`).
    let x_label_reach = match &x_dates {
        Some(axis) => axis.label_reach(),
        None => x_label_ws.iter().fold(0.0_f64, |reach, lw| {
            let mut ink = vega_fit::text_ink(0.0, 0.0, *lw, x_label_fs, x_align, x_baseline);
            if x_angle != 0.0 {
                ink = ink.rotate(x_angle, 0.0, 0.0);
            }
            reach.max(match x_edge {
                crate::common::XEdge::Bottom => ink.y2,
                crate::common::XEdge::Top => -ink.y1,
            })
        }),
    };

    // ── Legend ────────────────────────────────────────────────────────────
    let has_legend = r.legend.visible;
    let legend_edge = r.legend.position.edge.as_str();
    let legend_floats = r.legend.position.overlay;
    let grad_id = bound.gradient_id.as_str();
    let leg_label_font = &r.legend.label.font;
    let leg_label_size = leg_label_font.size_or(11.0);
    let leg_label_family = leg_label_font.family_or("sans-serif");
    let leg_title_font = &r.legend.title.font;
    let leg_title_size = leg_title_font.size_or(12.0);
    let leg_title_family = leg_title_font.family_or("sans-serif");
    let leg_label_offset = r
        .legend
        .label
        .padding
        .ok_or("heatmap: legend.label.padding absent (theme required)")?;
    let leg_labels = &bound.gradient_labels;
    // A time-scale legend's labels follow its drawn length: two ticks below
    // a 200px bar, four at 200.
    let date_label_widths = match &bound.coloring {
        Coloring::DateGradient { min, max, aware } => {
            let widest = |length: f64| -> Result<Option<f64>, String> {
                let labels = date_legend_values(*min, *max, length, r.legend.values.as_deref(), *aware)?
                    .into_iter()
                    .map(|day| legend_date_label(day, *aware))
                    .collect::<Result<Vec<_>, _>>()?;
                measure.widest_shaped(&labels, leg_label_family, leg_label_size)
            };
            Some((widest(VEGA_GRADIENT_LENGTH_MIN)?, widest(VEGA_GRADIENT_LENGTH_MAX)?))
        }
        _ => None,
    };
    let leg_title_text = &bound.legend_title;
    let leg_title_w = if leg_title_text.is_empty() {
        0.0
    } else {
        fonts.measure_shaped(leg_title_text, leg_title_family, leg_title_size)?
    };
    // A nominal color's symbol legend: a fill-only square per category. The
    // mirror's ghost rule layer strokes the same color, which turns a
    // numeric gradient into a symbol legend too: a stroked circle per rung.
    let ladder_symbols = mirror_active && !matches!(bound.coloring, Coloring::Categories(_));
    let symbol_entries: Vec<LegendEntry> = match &bound.coloring {
        Coloring::Categories(scale) => category_legend_entries(scale, r.legend.values.as_deref()),
        _ => bound
            .ladder
            .iter()
            .map(|(label, fill)| LegendEntry { label, fill, symbol: Some(LegendSymbol::Circle), series_key: None, recede: None })
            .collect(),
    };
    let symbol_legend_box = (has_legend && (ladder_symbols || matches!(bound.coloring, Coloring::Categories(_))))
        .then(|| vega_legend(&symbol_entries, false, leg_title_text, &r.legend, fonts))
        .transpose()?;
    let gradient = has_legend
        && !ladder_symbols
        && matches!(bound.coloring, Coloring::Gradient { .. } | Coloring::DateGradient { .. });
    let vertical_gradient = gradient && r.legend.direction == "vertical";
    let gradient_legend = match &bound.coloring {
        Coloring::Gradient { min, max, ticks, scale } if gradient => {
            let colors = GradientColors::new(&r.style.color_gradient, scale.as_ref());
            Some(GradientLegend::resolve(r, colors, grad_id, (*min, *max, ticks), leg_labels, fonts)?.with_title(
                &r.legend,
                leg_title_text.clone(),
                fonts,
            )?)
        }
        // A horizontal date bar's box holds no labels; its ladder is drawn at paint, by length.
        Coloring::DateGradient { min, max, .. } if gradient && !vertical_gradient => Some(
            GradientLegend::over(&r.legend, GradientPaint::Stops(&[]), grad_id, (*min, *max, &[]), &[], fonts)?
                .with_title(&r.legend, leg_title_text.clone(), fonts)?,
        ),
        _ => None,
    };
    // The legend box `(w, h)` at plot size `(pw, ph)`: a vertical gradient
    // runs `clamp(ph, 64, 200)` beside its labels, a horizontal one
    // `horizontal_gradient_length` above them; either sits under the title
    // band, and the box is `ceil(max(title, entries))` wide.
    let legend_size = |pw: f64, ph: f64| -> Option<(f64, f64)> {
        if let Some(legend) = &symbol_legend_box {
            Some((legend.box_w, legend.box_h()))
        } else if let Some(legend) = &gradient_legend {
            let bar_len = if vertical_gradient {
                legend.bar_len(pw, ph)
            } else {
                horizontal_gradient_length(legend_edge, legend_floats, pw)
            };
            Some((legend.box_width(bar_len), legend.height(bar_len)))
        } else if vertical_gradient {
            let (short, long) = date_label_widths.expect("the numeric gradient is drawn by gradient_legend");
            let label_w = if gradient_length(ph) >= VEGA_GRADIENT_LENGTH_MAX { long } else { short };
            Some(gradient_legend_box(leg_title_w, leg_title_size, label_w, leg_label_offset, gradient_length(ph)))
        } else {
            None
        }
    };
    // The legend's origin in plot coordinates, set on every fit pass (the
    // last is the fitted layout).
    let legend_xy = std::cell::Cell::new((0.0, 0.0));

    // ── Title ─────────────────────────────────────────────────────────────
    // Heatmap is a plain unit spec here -- a `multiples` chart took
    // `heatmap_facet::render` -- no concat -- so `_render_vl_artifact` runs `apply_title_overflow_to_spec`
    // over it like every other non-hconcat family: the compiled spec carries
    // the pass's own `title.limit` (568 on a 600-wide card with 16px padding).
    // Same law, same helper as pie and both line arms.
    let title = FrameTitle::new(
        r.title.as_deref(),
        r.subtitle.as_deref(),
        r.style.title_font.as_ref(),
        r.title_style.as_ref(),
        frame.avail_w(),
        fonts,
    )?;
    diagnostics.extend(title.truncations(&r.id));

    // Each date label's block (both rows) and whether Vega's greedy overlap
    // pass keeps it, for a plot `pw` x `ph`: ink and visibility both follow
    // the band spacing, so they are read per fit pass.
    let y_date_ink = |edge: &YEdge, pw: f64, ph: f64| -> Vec<Bounds> {
        let ys = BandScale::new(y_domain.clone(), 0.0, ph, 0.0, 0.0);
        y_dates
            .iter()
            .flatten()
            .map(|label| {
                let cy = ys.position(&y_domain[label.domain_idx]).expect("a label names a band of its own domain")
                    + ys.bandwidth() / 2.0
                    - AXIS_TRANSLATE;
                y_ink(edge, y_anchor_x(edge, pw), cy, label.w1.max(label.w2).min(y_label_limit), usize::from(!label.row2.is_empty()))
            })
            .collect()
    };
    let y_date_visible = |pw: f64, ph: f64| -> Vec<bool> {
        let ink = y_date_ink(&y_edges[0], pw, ph);
        match y_greedy_gap {
            Some(gap) => vega_fit::greedy_visible(&ink, gap),
            None => vec![true; ink.len()],
        }
    };

    // ── Plot rect: Vega's autosize-fit negotiation ────────────────────────
    let m = frame.fit(&title, |pw, ph, b| {
        // The y axis's own bounds join `xBounds`, the x axis's `yBounds`
        // (`ViewLayout.js:62-65`); the cells are bare rect marks.
        let mut y_axis = Bounds::new(0.0, 0.0, pw, ph);
        let ys = BandScale::new(y_domain.clone(), 0.0, ph, 0.0, 0.0);
        // The axis group sits a crisp half pixel above its labels' own
        // coordinates in the bounds Vega reads (`axis.js:77`), which only
        // shows where a label overhangs the plot top.
        for edge in &y_edges {
            if y_labels_hidden {
                // Hidden labels leave the axis its ticks alone.
                if y_tick_len > 0.0 {
                    let (x1, x2) = if edge.right { (pw, pw + y_tick_len) } else { (-y_tick_len, 0.0) };
                    for cat in &y_label_bands {
                        let Some(by) = ys.position(cat) else { continue };
                        let cy = by + ys.bandwidth() / 2.0 - AXIS_TRANSLATE;
                        y_axis.union(&Bounds::new(x1, cy, x2, cy));
                    }
                }
            } else if y_dates.is_some() {
                let visible = y_date_visible(pw, ph);
                for (ink, _) in y_date_ink(edge, pw, ph).iter().zip(&visible).filter(|(_, v)| **v) {
                    y_axis.union(ink);
                }
            } else {
                let ws = edge.relabeled.as_ref().map_or(&y_label_ws, |(_, ws)| ws);
                for (cat, lw) in y_label_bands.iter().zip(ws) {
                    let Some(by) = ys.position(cat) else { continue };
                    let cy = by + ys.bandwidth() / 2.0 - AXIS_TRANSLATE;
                    y_axis.union(&y_ink(edge, y_anchor_x(edge, pw), cy, *lw, 0));
                }
            }
        }
        if y_title_extra > 0.0 {
            let x = if y_right { pw + y_label_edge + y_title_extra } else { y_label_edge - y_title_extra };
            y_axis.union(&Bounds::new(x, ph / 2.0 - y_title_w / 2.0, x, ph / 2.0 + y_title_w / 2.0));
            if !y_right {
                y_axis.union(&Bounds::new(x, 0.0, 0.0, ph));
            } else {
                y_axis.union(&Bounds::new(pw, 0.0, x, ph));
            }
        }
        b.union(&y_axis);
        let reach = bound.marks.reach;
        b.union(&Bounds::new(-reach, -reach, pw + reach, ph + reach));
        let mut x_axis = Bounds::new(0.0, 0.0, pw, ph);
        let xs = BandScale::new(x_domain.clone(), 0.0, pw, 0.0, 0.0);
        let row_y = x_edge.at(ph, x_axis_offset + x_tick_len + x_label_pad);
        if let Some(axis) = &x_dates {
            axis.union_ink(&mut x_axis, pw, ph);
        }
        for (cat, lw) in x_domain.iter().zip(&x_label_ws).filter(|_| x_dates.is_none()) {
            let Some(bx) = xs.position(cat) else { continue };
            let cx = bx + xs.bandwidth() / 2.0;
            let mut ink =
                vega_fit::text_ink(cx, row_y, *lw, x_label_fs, x_align, x_baseline);
            if x_angle != 0.0 {
                // Vega rotates the text box about its anchor
                // (`text.js bound()` -> `Bounds.rotate`).
                ink = ink.rotate(x_angle, cx, row_y);
            }
            x_axis.union(&ink);
        }
        b.union(&x_axis);
        if x_title_extra > 0.0 {
            let (y1, y2) = match x_edge {
                crate::common::XEdge::Bottom => (0.0, ph + (b.y2 - ph).max(0.0) + x_title_extra),
                crate::common::XEdge::Top => (-((-b.y1).max(0.0) + x_title_extra), ph),
            };
            x_axis.union(&Bounds::new(pw / 2.0 - x_title_w / 2.0, y1, pw / 2.0 + x_title_w / 2.0, y2));
            b.union(&x_axis);
        }
        // Only the legend's own axis joins the fit (probed at nine spec
        // heights whose right box overhangs the plot by 1-70px, the
        // bottom margin stayed on the x-label band every time).
        if let Some(size) = legend_size(pw, ph) {
            let origin = legend_origin(&r.legend.position, (y_axis.x1, y_axis.x2), (0.0, x_axis.y2), (pw, ph), size);
            legend_xy.set(origin);
            if let Some(reach) = legend_fit_bounds(&r.legend.position, origin, size) {
                b.union(&reach);
            }
        }
    });
    let (plot_left, plot_top) = frame.origin(&m);
    let (plot_w, plot_h) = (m.plot_w, m.plot_h);
    let x_scale = BandScale::new(x_domain.clone(), 0.0, plot_w, 0.0, 0.0);
    let y_scale = BandScale::new(y_domain.clone(), 0.0, plot_h, 0.0, 0.0);
    if crate::labels_expr::view::active() {
        use crate::labels_expr::view::{anchor, register_axis, register_band, AxisSpec, Orient, Span};
        register_band("x", x_domain, 0.0, 0.0, Span::Width)?;
        register_band("y", y_domain, 0.0, 0.0, Span::HeightDown)?;
        if let Some(axis) = &x_dates {
            register_axis("x", axis.item_axis(ax_x)?);
        } else {
            let (align, baseline) = (anchor(x_align)?, anchor(x_baseline)?);
            let spec = AxisSpec { orient: Orient::Bottom, offset: x_tick_len + x_label_pad, shift: -0.5, align: Some(align), baseline: Some(baseline), angle: x_angle, flush: None, style: ax_x.labels.item_style()? };
            register_axis("x", spec);
        }
        if y_dates.is_none() {
            let edge = &y_edges[0];
            let baseline = anchor(if y_angle == 0.0 { "middle" } else { edge.baseline })?;
            let orient = if edge.right { Orient::Right } else { Orient::Left };
            let spec = AxisSpec { orient, offset: y_gutter, shift: -0.5, align: Some(anchor(edge.align)?), baseline: Some(baseline), angle: y_angle, flush: None, style: ax_y.labels.item_style()? };
            register_axis("y", spec);
        }
    }
    let cell_w = x_scale.bandwidth();
    let cell_h = y_scale.bandwidth();

    // ── Nodes ─────────────────────────────────────────────────────────────
    let mut body = title.nodes(m.title_pos, (plot_left, plot_top));

    if let (true, Coloring::DateGradient { min, max, aware }) = (vertical_gradient, &bound.coloring) {
        body.push(date_gradient_def(grad_id, *min, *max, *aware)?);
    }

    // ── Cells ─────────────────────────────────────────────────────────────
    body.push(Node::group(
        Attrs::new().set("transform", format!("translate({plot_left:.2},{plot_top:.2})")),
        cell_nodes(&bound.cells, &x_scale, &y_scale, &bound.marks)?,
    ));

    // ── X axis ────────────────────────────────────────────────────────────
    // The axis group sits at (0.5, plot_h + offset + 0.5); a label keeps its
    // exact band position inside it, a tick snaps to a whole pixel first
    // (`floor(center) + 0.5` -- probed over 64 band centers, widths 600-615).
    let x_sign = x_edge.sign();
    let x_axis_top = plot_top + x_edge.at(plot_h, x_axis_offset) + AXIS_TRANSLATE;
    let x_label_row = x_axis_top + x_sign * (x_tick_len + x_label_pad);
    let x_label_color = ax_x.labels.font.color.as_deref().unwrap_or("#626366");
    let x_label_weight = ax_x.labels.font.weight_str();
    let x_title_y = x_label_row
        + x_sign * (x_label_reach + ax_x.title.padding.unwrap_or(10.0))
        + measure::baseline_shift(x_edge.baseline(), ax_x.title.font.size_or(11.0));
    let x_title = crate::common::axis_title_at(&x_title_text, &ax_x.title, plot_left + plot_w / 2.0, x_title_y, None);
    let x_axis = if let Some(axis) = &x_dates {
        let mut nodes = axis.nodes(ax_x, plot_left, plot_w, (plot_top + x_edge.edge_y(plot_h), plot_top + x_edge.edge_y(plot_h)))?;
        nodes.extend(x_title);
        nodes
    } else {
        let caption = category_axis_caption(&x_title_text, ax_x, x_domain)?;
        let mut items = Vec::new();
        if x_tick_visible {
            let xs = (0..x_texts.len()).map(|i| plot_left + (x_scale.position_at(i) + cell_w / 2.0).floor() + AXIS_TRANSLATE);
            items.extend(crate::common::x_tick_marks(&ax_x.ticks, None, &[], xs, (plot_top + x_edge.edge_y(plot_h), x_axis_top + x_sign * x_tick_len), 2)?);
        }
        for (i, band_text) in x_texts.iter().enumerate() {
            let center = x_scale.position_at(i) + cell_w / 2.0;
            let label_x = plot_left + center;
            let dy = measure::baseline_shift(x_baseline, x_label_fs);
            let face = LabelFace {
                places: 2,
                anchor: svg_text_anchor(x_align),
                family: x_label_family,
                size: x_label_fs,
                weight: &x_label_weight,
                style: ax_x.labels.font.style.as_deref(),
                fill: x_label_color,
            };
            items.push(if x_angle == 0.0 {
                flat_label((label_x, x_label_row + dy), &face, false, band_text)
            } else {
                rotated_label((label_x, x_label_row, x_angle, dy), &face, (band_text, ""), false)
            });
        }
        items.extend(x_title);
        crate::contract::axis_group(crate::contract::AxisChannel::X, &caption, (plot_left + AXIS_TRANSLATE, x_axis_top), items)?
    };

    // ── Y axis ────────────────────────────────────────────────────────────
    let y_label_color = ax_y.labels.font.color.as_deref().unwrap_or("#626366");
    let y_label_weight = ax_y.labels.font.weight_str();
    let y_tick_color = crate::common::required_ink(ax_y.ticks.color.as_deref(), "heatmap", "axis_y.ticks.color")?;
    let y_tick_w_px = ax_y.ticks.width.unwrap_or(1.0);
    let y_visible = y_date_visible(plot_w, plot_h);
    let y_rows: Vec<(&str, &str)> = match &y_dates {
        Some(labels) => labels.iter().map(|l| (y_domain[l.domain_idx].as_str(), l.row1.as_str())).collect(),
        None => y_domain.iter().zip(y_label_texts).map(|(d, text)| (d.as_str(), text.as_str())).collect(),
    };
    let y_caption = category_axis_caption(&y_title_text, ax_y, y_domain)?;
    let mut y_axes = Vec::new();
    for (edge_idx, edge) in y_edges.iter().enumerate() {
        let y_text_anchor = svg_text_anchor(edge.align);
        let y_axis_x = plot_left + if edge.right { plot_w } else { 0.0 } + AXIS_TRANSLATE;
        let y_label_x = if edge.right { y_axis_x + y_gutter } else { y_axis_x - y_gutter };
        // One row per drawn label: every band for a category y, only the ladder's
        // openers (and a second context row under them) for a date y.
        let mut items = Vec::new();
        for (i, &(band_val, row1)) in y_rows.iter().enumerate() {
            let row1 = edge.relabeled.as_ref().map_or(row1, |(texts, _)| texts[i].as_str());
            let Some(band_y) = y_scale.position(band_val) else { continue };
            let center = band_y + cell_h / 2.0;
            if y_tick_visible {
                let tick_y = plot_top + center.floor() + AXIS_TRANSLATE;
                let tick_x = if edge.right { y_axis_x + y_tick_len } else { y_axis_x - y_tick_len };
                items.push(tick_line(2, (tick_x, tick_y), (y_axis_x, tick_y), y_tick_color, y_tick_w_px));
            }
            if y_labels_hidden {
                continue;
            }
            let row2 = y_dates.as_ref().map(|labels| labels[i].row2.as_str()).unwrap_or("");
            let hidden = y_dates.is_some() && !y_visible[i];
            let face = LabelFace {
                places: 2,
                anchor: y_text_anchor,
                family: y_label_family,
                size: y_label_fs,
                weight: &y_label_weight,
                style: ax_y.labels.font.style.as_deref(),
                fill: y_label_color,
            };
            if y_angle != 0.0 {
                items.push(rotated_label(
                    (y_label_x, plot_top + center, y_angle, measure::baseline_shift(edge.baseline, y_label_fs)),
                    &face,
                    (row1, row2),
                    hidden,
                ));
                continue;
            }
            let row1_y = plot_top + center + measure::baseline_shift("middle", y_label_fs);
            for (text, y) in [(row1, row1_y), (row2, row1_y + vega_fit::label_row_advance(y_label_fs))] {
                if !text.is_empty() {
                    items.push(flat_label((y_label_x, y), &face, hidden, text));
                }
            }
        }
        // The ghost axis carries no title (`mirror_axis.py` nulls it).
        if edge_idx == 0 {
            items.extend(crate::common::axis_title_rotated(
                &y_title_text,
                &ax_y.title,
                y_axis_x + y_label_edge,
                plot_top + plot_h / 2.0,
                if y_right { 1.0 } else { -1.0 },
                None,
            ));
        }
        let caption = if edge_idx == 0 { y_caption.clone() } else { y_caption.untitled() };
        y_axes.push(crate::contract::axis_group(crate::contract::AxisChannel::Y, &caption, (y_axis_x, plot_top), items)?);
    }
    // A mirrored y axis lists its primary ahead of the x axis and the ghost after it.
    let mut y_axes = y_axes.into_iter();
    let primary = y_axes.next().expect("the primary y edge is always drawn");
    if mirror_active {
        body.extend(primary);
        body.extend(x_axis);
    } else {
        body.extend(x_axis);
        body.extend(primary);
    }
    body.extend(y_axes.flatten());

    // ── Legend ────────────────────────────────────────────────────────────
    // A symbol legend slides inside its own paint arm; a gradient block
    // slides here, over the box `legend_size` measures.
    let (grad_dx, grad_dy) = match (symbol_legend_box.is_none(), legend_size(plot_w, plot_h)) {
        (true, Some(block)) => slide_block(&r.id, &r.legend.position, block, (plot_w, plot_h))?,
        _ => (0.0, 0.0),
    };
    let (leg_x, leg_y) = (plot_left + legend_xy.get().0 + grad_dx, plot_top + legend_xy.get().1 + grad_dy);
    if let Some(legend) = gradient_legend.as_ref().filter(|_| !vertical_gradient) {
        if let Coloring::DateGradient { min, max, aware } = &bound.coloring {
            let dates = date_scale(*min, *max, *aware);
            body.extend(GradientAt::nodes(&r.legend, None, &dates, (grad_id, leg_title_text), fonts, (plot_w, plot_h), (leg_x, leg_y))?);
        } else {
            body.extend(legend.nodes(leg_x, leg_y, horizontal_gradient_length(legend_edge, legend_floats, plot_w)));
        }
    }
    match (&symbol_legend_box, &bound.coloring) {
        (Some(legend), _) => {
            let (dx, dy) = legend_slide(&r.id, &r.legend.position, legend, (plot_w, plot_h))?;
            body.extend(drawn_symbol_legend(&symbol_entries, legend, &r.legend, LegendSymbol::FilledSquare, leg_x + dx, leg_y + dy, false));
        }
        (None, Coloring::Gradient { .. }) if vertical_gradient => {
            if let Some(legend) = &gradient_legend {
                body.extend(legend.nodes(leg_x, leg_y, legend.bar_len(plot_w, plot_h)));
            }
        }
        (None, Coloring::DateGradient { min, max, aware }) if vertical_gradient => {
            let ticks = date_legend_values(*min, *max, plot_h, r.legend.values.as_deref(), *aware)?;
            let labels = ticks.iter().map(|d| legend_date_label(*d, *aware)).collect::<Result<Vec<_>, _>>()?;
            let bar = VerticalGradient {
                gradient_id: grad_id,
                title: leg_title_text,
                range: (*min, *max),
                values: &ticks,
                labels: &labels,
                length: gradient_length(plot_h),
            };
            body.extend(bar.nodes(&r.legend, leg_x, leg_y)?);
        }
        _ => {}
    }

    let canvas = Rect { x: 0.0, y: 0.0, width: frame.view_w, height: frame.h };
    let data_box = Rect { x: plot_left, y: plot_top, width: plot_w, height: plot_h };
    Ok(Layout {
        insets: canvas.insets_of(&data_box),
        data_box,
        nodes: vec![frame.svg(body)],
        diagnostics,
        title_pos: m.title_pos,
    })
}
