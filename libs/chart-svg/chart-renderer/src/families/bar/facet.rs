//! Small multiples: one panel per slice of the rows, composed into a single card by `facet_compose`.

use crate::diagnostic::Diagnostic;
use crate::families::facet_compose::{GhostStyle, PanelGeometry};
use crate::families::layers::YSides;
use crate::families::{facet_compose, facet_title, heatmap_facet, multiples};
use crate::{Payload, codes};

use measure::FontBook;
use serde_json::{Value, json};
use std::cell::{Cell, RefCell};
use std::collections::HashSet;

use super::bind::validate_layers;
use super::continuous::{XLayers, render_vertical_continuous_x_panel};
use super::domain::{CenterStack, horizontal_min_height};
use super::horizontal::{HorizontalFacetOpts, HorizontalFacetPanel, RightEdge, RowAxisPlacement, horizontal_layer_rows, render_horizontal_panel};
use super::layout::{XScale, warn_vertical_density};
use super::vertical::{BarFacetPanelOpts, render_vertical_panel};
use super::{BarResolved, x_resolution};

/// `_is_color_1to1_with_x` over the chart's data: pooled for a single chart,
/// folded per panel under small multiples.
pub(super) fn color_one_to_one(r: &BarResolved, payload: &Payload, color: &str) -> Result<bool, String> {
    if r.multiples.is_none() {
        return Ok(color_1to1_with_cat(&payload.rows, &r.x, color));
    }
    Ok(color_1to1_across_panels(&facet_panels(r, &payload.rows)?, &r.x, color))
}

/// The rows a panel's category domain reads: its own, plus those dropped for a null numeric color.
pub(super) fn panel_cat_rows<'a>(
    all: Option<&'a [multiples::Panel]>,
    panel: &'a multiples::Panel,
) -> &'a [serde_json::Map<String, Value>] {
    all.and_then(|all| all.iter().find(|p| p.key == panel.key)).map_or(panel.rows.as_slice(), |p| p.rows.as_slice())
}

/// The chart's rows regrouped into its facet panels, with the category and
/// color fields restored when one of them is itself a facet field.
pub(super) fn facet_panels(r: &BarResolved, rows: &[serde_json::Map<String, Value>]) -> Result<Vec<multiples::Panel>, String> {
    let mut panels = multiples::regroup(&r.panel_axes, rows).map_err(|e| format!("bar: {e}"))?;
    for field in std::iter::once(r.x.as_str()).chain(r.color.as_deref()) {
        multiples::restamp(&mut panels, &r.panel_axes, field);
    }
    Ok(panels)
}

/// `emitters/bar.py::_is_color_1to1_with_x` folded per panel: every panel
/// holding both fields must be 1:1 on its own, and at least one must (a
/// panel with neither field abstains, like `is_1to1`'s `None`).
pub(super) fn color_1to1_across_panels(panels: &[multiples::Panel], cat_field: &str, color_field: &str) -> bool {
    let mut checked = false;
    for panel in panels {
        if !panel.rows.iter().any(|row| row.contains_key(cat_field) && row.contains_key(color_field)) {
            continue;
        }
        checked = true;
        if !color_1to1_with_cat(&panel.rows, cat_field, color_field) {
            return false;
        }
    }
    checked
}

/// Production's `_is_color_1to1_with_x` (emitters/bar.py): true when every
/// category maps to exactly one color value (each band a single segment).
/// When it holds, #8118's palette assignment keys slots off the alphabetical
/// color domain — VL's own default — instead of the baseline-first stack
/// order (`palette_order = series if _is_color_1to1_with_x else order`).
/// Values compare by JSON encoding: numeric `2019` stays distinct from
/// `"2019"`, like production's raw-value sets, but numerically-equal
/// spellings (`10` vs `10.0`) stay distinct here where Python pools them —
/// a dataset spelling one x value two ways with two colors reads 1:1 here
/// and not in production. Rows missing either field are skipped, and an
/// all-missing dataset is not 1:1.
pub(super) fn color_1to1_with_cat(
    rows: &[serde_json::Map<String, Value>],
    cat_field: &str,
    color_field: &str,
) -> bool {
    let mut unique_cat = HashSet::new();
    let mut unique_pairs = HashSet::new();
    let mut checked = false;
    for row in rows {
        let (Some(cat), Some(color)) = (row.get(cat_field), row.get(color_field)) else {
            continue;
        };
        checked = true;
        unique_cat.insert(cat.to_string());
        unique_pairs.insert((cat.to_string(), color.to_string()));
    }
    checked && unique_cat.len() == unique_pairs.len()
}

/// Small-multiples composite entry point for bar — `render`'s own gate calls
/// this instead of `render_vertical` whenever `r.multiples` is set.
///
/// Composes N `render_vertical_panel` calls (one per `families::multiples::
/// regroup` panel) into one faceted card via `facet_layout::grid_layout`
/// (Vega's own `gridLayout`, `families/facet_layout.rs`), mirroring
/// `vega_lite.py::_apply_facet_layout` (per-panel width/height budget) and
/// `converters/chart.py::_correct_facet_overshoot` (single-pass shrink to
/// absorb decoration overshoot) — but measured from a REAL per-panel render
/// (this crate has no scenegraph to probe), not vl-convert's own probe
/// render.
///
/// A horizontal bar composes `render_horizontal_panel` panels instead: cell ink
/// only, with the category axis in the composer's `row_header` (or `row_footer`)
/// and the value axis in its `column_footer`.
///
/// The vertical arm's scope (refuses outside it rather than approximate):
/// `multiples.rows` and/or `multiples.columns` (a sparse grid fills the slots
/// after Vega's empty ones, `multiples::empty_leading_cells`), a color channel
/// only with the legend on top (`BarFacetPanelOpts`: one chart-level legend,
/// palette, category domain and `xOffset` verdict), no support_table
/// (production's `FacetFeature` refuses it alongside `multiples`), layers over
/// the base query (no dual axis, none on a continuous x), no
/// `axis_y.position: "left"` on a vertical
/// multi-column facet (`paint_value_axis` hardcodes the right-side "last
/// column" law). `axis_y.mirror:
/// true` is admitted on rows-only, columns-only, and grid shapes with a
/// right-side axis — see `row_header_offset`'s
/// own doc comment for the additive row-header law a row facet combines it
/// with — but refuses on a `position: "left"` facet of any shape (the
/// ghost's own edge-opposite-the-primary law is unmodeled), and on a mirror object carrying `format`/`expr` (a
/// per-edge relabel; the ghost here repaints the primary's own labels
/// verbatim, never re-formatted) or a `ruler` that differs from the primary's
/// (`AxisStyle::mirror_requested`, the one reader of the request; a ruler
/// equal to the primary's is what production bakes for a ruled right-edge
/// primary, and the verbatim repaint is already correct for it).
/// `multiples.scale: "independent"` paints and folds every panel's own value
/// axis; a mirror under it is production's resolve error.
pub(super) fn render_facet(bound: super::bind::Bound<'_>, fonts: &FontBook) -> Result<String, Diagnostic> {
    let super::bind::Bound { payload, mut r, wide_series, .. } = bound;
    let payload: &Payload = &payload;
    let wide_series: &[String] = &wide_series;
    let horizontal = r.orientation == "horizontal";
    let mc = r.multiples.clone().expect("render's own gate checked r.multiples.is_some()");
    // A facet's color legend sits once beside or above the grid
    // (`_multiples_wants_top_legend` moves it to the top by default).
    let legend_edge = if (r.color.is_some() || !r.layers.is_empty()) && r.legend.visible {
        facet_compose::facet_legend_edge(&r.legend.position)
    } else {
        facet_compose::LegendEdge::Top
    };
    if r.panel_axes.is_empty() {
        return Err(
            "bar: multiples with no baked panel_axes — resolve() should always supply at least one".into(),
        );
    }
    let axes = r.panel_axes.clone();

    let panels = facet_panels(&r, &payload.rows)?;
    // Rows dropped for a null numeric color keep their category in their panel's domain.
    let all_panels = r.all_rows.as_deref().map(|all| facet_panels(&r, all)).transpose()?;
    let chart_cat_rows = r.all_rows.as_deref().unwrap_or(&payload.rows);
    if panels.is_empty() {
        return Err("bar: multiples produced zero panels — no row matched the baked panel_axes".into());
    }
    let panel_rows_n = multiples::panel_axis_cardinality(&axes, mc.rows.as_deref());
    let panel_cols_n = multiples::panel_axis_cardinality(&axes, mc.columns.as_deref());
    let missing = multiples::empty_leading_cells(panels.len(), panel_rows_n, panel_cols_n)?;
    // A titled value axis paints once, at the last column of each row.
    // `regroup`'s own panel order is `axes`' cartesian product, rows outer /
    // columns inner (`multiples.rs`'s own doc comment) — `panels[row *
    // panel_cols_n + col]` is row-major, matching `facet_layout::grid_layout`'s
    // own `i / ncols` = row, `i % ncols` = col indexing exactly, so no
    // reshaping is needed between `regroup`'s output and the grid composer's
    // input. `axes` carries one `PartitionAxis` per SET dimension (one entry
    // for a rows-only or columns-only facet, two for a grid) — these indices
    // say which of a panel's own `key` entries is the row value vs the
    // column value, since position alone doesn't (a columns-only facet's
    // sole axis is `key[0]`, not a row).
    let row_axis_idx = mc.rows.as_deref().and_then(|f| axes.iter().position(|a| a.field == f));
    let col_axis_idx = mc.columns.as_deref().and_then(|f| axes.iter().position(|a| a.field == f));

    let has_mirror = r.style.axis_y.mirror_requested("bar")?;
    // A left value axis rides the composer's `row_header`, with the mirror's ghost on the right edge: the
    // picture of a right axis with start-aligned labels and its left ghost, so the panels render that.
    let left_primary = !horizontal && mc.scale != "independent" && r.style.axis_y.position.as_deref() == Some("left");
    let left_title_text = crate::common::axis_title_text(&r.y, r.y_label.as_deref(), &r.field_labels)?;
    let left_title_style = r.style.axis_y.title.clone();
    if left_primary {
        r.style.axis_y.position = Some("right".into());
        r.style.axis_y.labels.align = None;
        r.style.axis_y.title.visible = Some(false);
    }
    let ghost_only = left_primary && !has_mirror;
    // The ghost loop anchors at the composite's LEFT edge (`mirror_axis.py:
    // 151-159` puts it opposite the primary); a left primary never reaches
    // it because every left-axis facet refuses above.
    //
    // `multiples.scale: "independent"`: real Vega drops the shared
    // `row_footer` role entirely and paints each cell's own value axis
    // INSIDE the cell (`fixtures/multiples/05-independent-scale`), so every
    // panel paints and folds its own axis, whatever the column count. A
    // mirror is a production resolve error under it.
    let independent = mc.scale == "independent";
    if independent && has_mirror {
        return Err(multiples::independent_mirror_error(payload.chart_id()?));
    }
    // `multiples.scale: "shared"` (the default) computes the measure domain
    // off the WHOLE chart's rows, fed to `render_vertical_panel` as an
    // explicit override; `"independent"` passes `None` instead, so each panel falls back to its own
    // `rows` subset — `render_vertical_panel`'s own `value_domain_rows` doc
    // comment names this exact case.
    let value_domain_rows: Option<&[serde_json::Map<String, Value>]> =
        if independent { None } else { Some(payload.rows.as_slice()) };
    // The chart's OWN effective background — see `BarResolved::background`.
    let bg = r.background.clone();
    // The title belongs to the facet root, not to any panel: built against the
    // padded card, then taken off `r` so no panel reserves or paints it.
    let pad = payload.padding.as_ref();
    let (title_text, subtitle_text) = (r.title.clone(), r.subtitle.clone());
    let (title_font, title_style) = (r.style.title_font.clone(), r.title_style.clone());
    let title_src = facet_title::TitleSource {
        family: "bar",
        title: title_text.as_deref(),
        subtitle: subtitle_text.as_deref(),
        title_font: title_font.as_ref(),
        title_style: title_style.as_ref(),
    };
    let title_plan = match facet_title::layout_title(
        &title_src,
        fonts,
        payload.width - pad.map_or(0.0, |p| p.left + p.right),
    )? {
        facet_title::TitleLayout::Plan(plan) => {
            plan.warn_truncated(&r.id);
            Some(plan)
        }
        facet_title::TitleLayout::Untitled => None,
    };
    r.title = None;
    r.subtitle = None;

    // `_domain_subset_narrowing_candidates`: x narrows per panel under a
    // columns-only facet (a rows or grid facet never narrows x), and Vega then
    // draws the x axis inside each cell. Only a discrete scale narrows
    // (`_discrete_facet_channels`): a continuous time scale stays shared.
    let x_discrete = matches!(
        x_resolution(&r, &payload.rows)?.resolution.vl_type,
        crate::families::temporal::VegaType::Ordinal | crate::families::temporal::VegaType::Nominal
    );
    let x_narrowed = !horizontal && x_discrete && mc.rows.is_none() && multiples::field_is_proper_subset_in_a_panel(&panels, &r.x);
    let color_one_to_one = r
        .color
        .as_deref()
        .is_some_and(|color| color_1to1_across_panels(&panels, &r.x, color));

    let layered = CenterStack::splits(r.style.mark.border.radius, &payload.rows, &r.y)
        || !r.layers.is_empty()
        || r.style.mark.labels.as_ref().is_some_and(|l| l.visible)
        || r.style.mark.total_label.as_ref().is_some_and(|t| t.visible == Some(true));
    let whole_chart = CenterStack::over(panels.iter().map(|p| p.rows.as_slice()), &r.x, &r.y, false);
    let center_stack = (r.stack == "center" && !layered).then_some(whole_chart);
    let series = Cell::new(1);
    // `_domain_subset_narrowing_candidates`: a horizontal bar's category rides VL
    // `y`, which narrows when some panel holds a proper subset of the categories
    // (never under a mirror or a layer), and Vega then draws the category axis
    // inside each cell. A columns facet pays for that extra axis in panel width
    // and narrows only while the panel stays legible (`extra_axis_is_affordable`).
    let cat_axis_px = if horizontal && mc.columns.is_some() {
        heatmap_facet::facet_extra_axis_px(
            &r.style.axis_x.labels,
            &heatmap_facet::first_appearance(&payload.rows, &r.x),
            fonts,
        )?
    } else {
        0.0
    };
    let cat_narrowed = horizontal
        && r.layers.is_empty()
        && !has_mirror
        && multiples::field_is_proper_subset_in_a_panel(&panels, &r.x)
        && multiples::extra_axis_is_affordable(
            multiples::facet_panel_width(payload.width, panel_cols_n, false, 0.0),
            cat_axis_px,
        );
    let extra_axis_px = if cat_narrowed { cat_axis_px } else { 0.0 };
    // `effective_height`: every panel needs room for its categories' labels, so
    // the card grows to hold that many rows of them.
    let floored;
    let payload = if horizontal {
        let n_categories = if cat_narrowed {
            panels.iter().map(|p| crate::common::count_horizontal_bar_categories(&p.rows, &r.x)).max().unwrap_or(0)
        } else {
            crate::common::count_horizontal_bar_categories(&payload.rows, &r.x)
        };
        let min_height = horizontal_min_height(&r, n_categories)? * panel_rows_n as f64;
        if let Some(authored) = payload.authored_height.filter(|&authored| n_categories > 0 && min_height > authored) {
            crate::diagnostic::warn(Diagnostic::new(
                &codes::WARN_LAYOUT_MIN_EXCEEDS_HEIGHT,
                [
                    ("authored_height", json!(authored)),
                    ("chart_id", json!(r.id)),
                    ("min_height", json!(min_height)),
                    ("n_categories", json!(n_categories)),
                ],
            ));
        }
        let height = payload.height.max(min_height);
        floored = Payload { height, ..payload.clone() };
        &floored
    } else {
        payload
    };
    let title_boxes = multiples::facet_title_boxes((payload.width, payload.height), (panel_cols_n, panel_rows_n), has_mirror, extra_axis_px);
    let row_axis_placement: RefCell<Option<RowAxisPlacement>> = RefCell::new(None);
    let layers = validate_layers(&r, payload)?;
    // Production's own compile fails on a pinned layer inside small multiples
    // (vl-convert: unrecognized scale `child_layer_0_y`).
    if YSides::of(&layers).is_some() {
        return Err(crate::refuse!("bar: multiples with a dual-axis layer not supported").into());
    }
    let x_layers = XLayers { all: &layers, payload };
    let render_pass = |pw: f64, ph: f64, value_axis_right_overhang: f64| -> Result<Vec<(String, PanelGeometry)>, Diagnostic> {
        panels
            .iter()
            .enumerate()
            .map(|(panel_i, panel)| {
                let panel_key = serde_json::to_string(&panel.key).expect("a panel key re-serializes");
                if horizontal {
                    let layer_rows = horizontal_layer_rows(payload, &r, &layers, Some(&panel.rows));
                    let opts = HorizontalFacetOpts {
                        panel_key: &panel_key,
                        panel_ordinal: panel_i,
                        chart_rows: &payload.rows,
                        cat_rows: if cat_narrowed { panel_cat_rows(all_panels.as_deref(), panel) } else { chart_cat_rows },
                        color_one_to_one,
                        value_domain_rows: if independent { &panel.rows } else { &payload.rows },
                        independent_scale: independent,
                        cell_cat_axis: cat_narrowed,
                        center_stack,
                        center_label_stack: whole_chart,
                        right_edge: RightEdge {
                            paint: (missing + panel_i) % panel_cols_n == panel_cols_n - 1,
                            fold: panel_cols_n == 1,
                            overhang: value_axis_right_overhang,
                        },
                        title_boxes,
                    };
                    let (svg, facet_panel) = render_horizontal_panel(
                        payload.chart_id()?,
                        None,
                        &panel.rows,
                        pw,
                        ph,
                        None,
                        &bg,
                        fonts,
                        &r,
                        wide_series,
                        &layer_rows,
                        None,
                        payload.board_style.as_ref(),
                        Some(&opts),
                    )?;
                    let HorizontalFacetPanel { geometry, row_axis } =
                        facet_panel.expect("a facet call returns its panel geometry");
                    row_axis_placement.replace(row_axis);
                    return Ok((print::print(&[svg.svg]), geometry));
                }
                // Real Vega's `row_footer`/`row_header` (the shared value
                // axis) appears ONCE PER ROW, at that row's own LAST
                // column — see `render_vertical_panel`'s own
                // `paint_value_axis` doc comment for the probe evidence.
                let paint_value_axis = independent || (missing + panel_i) % panel_cols_n == panel_cols_n - 1;
                // See `render_vertical_panel`'s own `fold_axis_gutter` doc
                // comment: folding is only correct when "once per row"
                // coincides with "once per panel" (`panel_cols_n == 1`);
                // a multi-column row reserves this once at the composer
                // level instead (`render_facet`'s own canvas-width
                // formula, below).
                let fold_axis_gutter = independent || panel_cols_n == 1;
                // Same arm-selection order `render()`'s own dispatch uses:
                // the quantitative-x arm first (`Ok(None)` for any sub-shape
                // it doesn't model), falling through to the band arm — a
                // per-panel call must try the same arms in the same order,
                // or a numeric-x facet silently renders on the wrong scale.
                // Every row suppresses its own x-axis, unconditionally —
                // real Vega's shared x-axis (`column_footer`) is a role
                // structurally SEPARATE from every row's own `cell`, painted
                // ONCE by the composer below (`paint_shared_x_axis`), never
                // squeezed into any one panel's own canvas.
                if let Some((svg, geom)) = render_vertical_continuous_x_panel(
                    &panel.rows,
                    &payload.rows,
                    pw,
                    ph,
                    None,
                    &bg,
                    fonts,
                    payload.board_style.as_ref(),
                    &r,
                    &x_layers,
                    true,
                    paint_value_axis,
                    fold_axis_gutter,
                    value_axis_right_overhang,
                    Some((&panel_key, panel_i)),
                    ghost_only,
                    left_primary,
                    Some(title_boxes),
                )? {
                    return Ok((print::print(&[svg.svg]), geom.expect("a facet panel is drawn with its geometry")));
                }
                let facet_opts = BarFacetPanelOpts {
                    title_boxes,
                    decision_w: multiples::facet_panel_width(payload.width, panel_cols_n, has_mirror, extra_axis_px),
                    suppress_x_axis: true,
                    cell_x_axis: x_narrowed,
                    paint_value_axis,
                    fold_axis_gutter,
                    value_domain_rows,
                    value_axis_right_overhang,
                    independent_scale: independent,
                    legend_swatch_stroke_is_fill: !horizontal && !independent && mc.columns.is_some() && r.layers.is_empty(),
                    chart_rows: payload.rows.as_slice(),
                    center_stack,
                    center_label_stack: whole_chart,
                    share_cat_domain: mc.rows.is_some(),
                    cat_rows: if mc.rows.is_some() { chart_cat_rows } else { panel_cat_rows(all_panels.as_deref(), panel) },
                    color_one_to_one,
                    has_mirror,
                    ghost_only,
                    left_primary,
                    series: &series,
                    panel_key: &panel_key,
                    panel_ordinal: panel_i,
                };
                let (svg, geom, _composed) = render_vertical_panel(
                    &panel.rows,
                    pw,
                    ph,
                    None,
                    &bg,
                    payload,
                    fonts,
                    &r,
                    None,
                    wide_series,
                    &layers,
                    Some(&facet_opts),
                )?;
                let geom = geom.expect("a facet panel is drawn with its geometry");
                Ok((
                    print::print(&[svg.svg]),
                    PanelGeometry {
                        content_start: geom.content_start,
                        svg_end: geom.svg_end,
                        ink: geom.ink,
                        axis_w_overhang: geom.axis_w_overhang,
                        // `geom.plot_left` is `m.left` alone (this call
                        // passes `pad: None`, so the panel's own internal
                        // `pad_l` is 0).
                        left_mark_overhang: geom.plot_left,
                        plot_top: geom.plot_top,
                        raw_right_reach: geom.raw_right_reach,
                        widest_val_tick_label_width: geom.widest_val_tick_label_width,
                        val_gutter: geom.val_gutter,
                        val_tick_size: geom.val_tick_size,
                        val_ticks: geom.val_ticks,
                        val_tick_marks: geom.val_tick_marks,
                        ghost_labels: geom.ghost_labels,
                        mirror_style: Some(GhostStyle {
                            caption: geom.val_caption,
                            val_font_size: geom.val_font_size,
                            val_weight: geom.val_weight,
                            val_color: geom.val_color,
                            val_family: geom.val_family,
                        }),
                        axis: geom.axis,
                        top_axis_overhang: geom.top_axis_overhang,
                        value_title_w: geom.value_title_w,
                        legend: geom.facet_legend,
                        value_axis: geom.value_axis,
                    },
                ))
            })
            .collect()
    };

    // Production's facet header reads the theme's base `font_sans` cascade
    // (`r.legend.label.font.family`), not either channel axis' own — see
    // `facet_compose::compose_facet`'s own doc comment for the full
    // derivation (probed against `fixtures/facet-panel-axis-title` and
    // `fixtures/multiples/01-rows-only`'s reference SVGs).
    let header_font_family =
        r.legend.label.font.family.clone().unwrap_or_else(|| "sans-serif".into());

    let spec = facet_compose::FacetSpec {
        family: "bar",
        panels: &panels,
        axes: &axes,
        panel_rows_n,
        panel_cols_n,
        row_axis_idx,
        col_axis_idx,
        has_mirror,
        // The panels paint a vertical bar's mirror themselves (a heatmap's `row_footer` copy),
        // and a horizontal bar's: its category axis' ghost on the far edge.
        left_axis_only: ghost_only,
        left_title: left_primary.then_some((left_title_text.as_str(), &left_title_style)),
        mirror_in_panel: horizontal,
        own_value_axes: independent && !horizontal,
        y_axes_in_cells: if horizontal { cat_narrowed } else { independent },
        extra_axis_px,
        // A horizontal bar swaps the screen axes: the value axis runs along the
        // footer, the category axis down the row header.
        cell_x_axis: if horizontal { independent } else { x_narrowed },
        axis_x: if horizontal { &r.style.axis_y } else { &r.style.axis_x },
        axis_y: if horizontal { &r.style.axis_x } else { &r.style.axis_y },
        header_font_family: &header_font_family,
        background: &bg,
        x_scale: if horizontal { crate::ViewScale::Continuous } else { crate::ViewScale::Discrete },
    };
    // One chart-level legend: Vega resolves the color scale once for the whole
    // composite, so it hangs above the grid as chrome. Every panel builds the
    // same one (chart-wide domain); the first render at the naive width lifts it.
    let facet_legend = if horizontal || r.color.is_some() || !r.layers.is_empty() {
        let naive_w = multiples::panel_width(
            payload,
            panel_cols_n,
            has_mirror,
            extra_axis_px,
            if horizontal { crate::ViewScale::Continuous } else { crate::ViewScale::Discrete },
        )?;
        let naive_h = payload.height / panel_rows_n as f64;
        render_pass(naive_w, naive_h, 0.0)?.into_iter().next().and_then(|(_, g)| g.legend)
    } else {
        None
    };
    // The probe above recorded where the shared category axis (`row_header`) paints.
    let cat_title = crate::common::wrapped_axis_title(
        &r.id,
        "x_label",
        &crate::common::axis_title_text(&r.x, r.x_label.as_deref(), &r.field_labels)?,
        &r.style.axis_x.title.font,
        title_boxes.1 .0,
        title_boxes.1 .1,
        fonts,
    )?;
    let row_axis_spec = row_axis_placement.take().filter(|_| !cat_narrowed).map(|placement| facet_compose::RowAxisSpec {
        title: if placement.titled { &cat_title } else { "" },
        title_style: &r.style.axis_x.title,
        reserve: placement.reserve,
        label_x: placement.label_x,
        label_anchor: placement.label_anchor,
        title_x: placement.title_x,
        hugs_labels: None,
    });
    let chrome = facet_compose::FacetChrome {
        title: title_plan.as_ref().map(|plan| facet_compose::TitleChrome {
            ink: plan.ink.clone(),
            paint: Box::new(|svg, x, y| plan.paint(&title_src, svg, x, y)),
        }),
        legends: facet_legend.as_ref().map(|lg| facet_compose::LegendChrome {
            edge: legend_edge,
            width: Box::new(|_, _| lg.box_w),
            height: Box::new(|_| lg.box_h),
            paint: Box::new(|svg, x, y, _, _| {
                svg.push_str(&print::print(&[lg.placed(x, y)]));
            }),
        }).into_iter().collect(),
    };
    let chrome = (chrome.title.is_some() || !chrome.legends.is_empty()).then_some(chrome);
    let svg = facet_compose::compose_facet_full(payload, fonts, &spec, chrome.as_ref(), row_axis_spec.as_ref(), &render_pass)?;
    if horizontal {
        return Ok(svg);
    }
    warn_vertical_density(
        payload.chart_id()?,
        &r,
        &payload.rows,
        fonts,
        payload.board_style.as_ref(),
        multiples::facet_panel_width(payload.width, panel_cols_n, has_mirror, 0.0),
        series.get(),
        XScale::of(&x_resolution(&r, &payload.rows)?.resolution),
    )?;
    Ok(svg)
}
