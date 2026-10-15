//! Vega's own facet composite grid sizing — a direct, function-for-function
//! port of `vega-view-transforms`'s `gridLayout` (vega 6.2.0,
//! `packages/vega-view-transforms/src/layout/grid.js`, same version
//! `vega_fit.rs` cites). This is the algorithm `render/converters/chart.py`'s
//! `_correct_facet_overshoot` measures indirectly (one `vegalite_to_
//! scenegraph` probe render); porting it lets a facet composite compute its
//! own composite width/height in closed form instead of shelling out to
//! vl-convert.
//!
//! Deliberately NOT ported: `layoutHeaders`/`layoutTitle`/`trellisLayout`
//! (row/column header, title, and shared-footer placement —
//! `facet_compose::compose_facet` computes its own row-header margin AND
//! the shared x-axis's own y (`footer_y`, `layoutHeaders`'s real `agg=max`
//! union over the last row's own real `ink.y2`, `Math.ceil`'d per
//! `grid.js:13`'s own `max` callback) via closed-form
//! reductions of `layoutHeaders`'s own real algorithm instead, covering
//! rows-only, columns-only, and grid facets, with or without `axis_y.
//! mirror`, for every family this composer serves (bar, line); a shape
//! that needs `Align::Each`/`bboxFlush` will need the real generic port,
//! not a second hand-rolled margin), `view.dirty(...)` redraw bookkeeping (no live
//! scenegraph to invalidate — this runs once, headless, before any SVG is
//! emitted), and the `mark.bounds` vs `item.bounds` split (an incremental-
//! redraw optimization; every consumer of this module wants the final
//! bounds only). `axisTitleLayout`/`axisLayout` are NOT ported here —
//! they position ONE axis mark group's own title within ITS OWN parent
//! bounds, a per-panel concern `render_vertical_panel`'s own `fit_plot`
//! call already resolves via `vega_fit.rs`, not a fact this module's own
//! grid composition needs.
//!
//! `Group`/row-header/row-footer/col-header/col-footer/row-title/col-title
//! role dispatch (`gridLayoutGroups`) has no analog here: this module's own
//! callers already know which of their own boxes are cells vs headers —
//! there is no live scenegraph tree to walk and classify by mark role.

use crate::vega_fit::Bounds;

/// One grid cell's own box — `groups[i]` in `gridLayout`'s own JS. `x`/`y`
/// are the cell's position BEFORE this layout runs (always `0.0` for a
/// freshly-composed facet, mirroring a fresh scenegraph group's own
/// zero-initialized position); `bounds` is the cell's LOCAL ink extent
/// (`bboxFull`'s own output: the group's rendered content bounds, already
/// translated so the group's own origin is `(0, 0)`).
#[derive(Clone, Copy)]
pub struct GridCell {
    pub x: f64,
    pub y: f64,
    pub bounds: Bounds,
}

impl GridCell {
    pub fn new(bounds: Bounds) -> Self {
        GridCell { x: 0.0, y: 0.0, bounds }
    }
}

/// `gridLayout`'s own options (`opt` in the JS) — this module's callers
/// pass the SAME `align`/`padding`/`columns` for both column and row
/// (production's own compiled `layout.align`/`layout.padding` are bare
/// scalars, not `{row, column}` objects, for every facet shape this port
/// reaches — see `families/multiples.rs`'s module doc). `align: "all"` and
/// `align: "none"` (`align_none`) are the modes callers author — `"each"`/positional-offset and
/// `bounds: "flush"` (`bboxFlush` over the `bounds: "full"` default) are
/// not ported; a re-added caller needing either should re-add the JS's own
/// `Align`/`bboxFlush` alongside it, not a speculative enum with one
/// variant.
pub struct GridOpt {
    pub padding: f64,
    pub columns: usize,
    /// `align: "none"` across columns: each column advances by its own cell's
    /// ink instead of one shared pitch (a facet whose x axis lives in every
    /// cell).
    pub align_none: bool,
}

/// `gridLayout` — positions `cells` into a `opt.columns`-wide grid,
/// returning each cell's own `(dx, dy)` offset (added to its existing
/// `x`/`y`, matching the JS's own `g.x += dx`) plus the grid's own total
/// bounds (anchored at `(0, 0)`: this port never uses `opt.anchor`/
/// `opt.center`, which every caller here leaves at their zero/off
/// defaults — see the module doc for what's deliberately unported).
pub fn grid_layout(cells: &[GridCell], opt: &GridOpt) -> (Vec<(f64, f64)>, Bounds) {
    let n = cells.len();
    let ncols = if opt.columns == 0 { n.max(1) } else { opt.columns };
    let mut x_offset = vec![0.0; n];
    let mut y_offset = vec![0.0; n];
    let mut x_max = 0.0_f64;
    let mut y_max = 0.0_f64;
    let boxes: Vec<Bounds> = cells.iter().map(|c| c.bounds).collect();

    for (i, b) in boxes.iter().enumerate() {
        x_max = x_max.max(b.x2.ceil());
        y_max = y_max.max(b.y2.ceil());
        x_offset[i] = opt.padding + offset_value(b.x1);
        y_offset[i] = opt.padding + offset_value(b.y1);
    }
    // set initial alignment offsets
    for i in 0..n {
        if i % ncols == 0 {
            x_offset[i] = 0.0;
        }
        if i < ncols {
            y_offset[i] = 0.0;
        }
    }

    // enforce column alignment constraints: `all` gives every column one
    // shared pitch; `none` advances each column by the previous cell's own
    // extent
    if opt.align_none {
        for i in 0..n {
            if i % ncols != 0 {
                x_offset[i] += boxes[i - 1].x2.ceil();
            }
        }
    } else {
        let mut offset = 0.0_f64;
        for i in 0..n {
            if i % ncols != 0 {
                offset = offset.max(x_offset[i]);
            }
        }
        for i in 0..n {
            if i % ncols != 0 {
                x_offset[i] = offset + x_max;
            }
        }
    }

    // enforce row alignment constraints
    let mut offset = 0.0_f64;
    for i in ncols..n {
        offset = offset.max(y_offset[i]);
    }
    for i in ncols..n {
        y_offset[i] = offset + y_max;
    }

    // perform horizontal grid layout
    let mut dx = vec![0.0; n];
    let mut dy = vec![0.0; n];
    let mut x = 0.0;
    for i in 0..n {
        x = x_offset[i] + if i % ncols != 0 { x } else { 0.0 };
        dx[i] += x - cells[i].x;
    }
    // perform vertical grid layout
    for c in 0..ncols {
        let mut y = 0.0;
        let mut i = c;
        while i < n {
            y += y_offset[i];
            dy[i] += y - cells[i].y;
            i += ncols;
        }
    }
    // No horizontal/vertical centering pass, and no anchor shift: every
    // caller here leaves `opt.center`/`opt.anchor` at JS's own zero/off
    // defaults (`get$1(opt.anchor, X)` reads `0` absent an authored
    // anchor, and `Math.round(0)` is a no-op) — see the module doc for
    // what's deliberately unported.
    let mut bounds = Bounds::empty_new();
    let mut offsets = Vec::with_capacity(n);
    for i in 0..n {
        let mut b = boxes[i];
        b.translate(dx[i], dy[i]);
        bounds.union(&b);
        offsets.push((dx[i], dy[i]));
    }
    (offsets, bounds)
}

/// `offsetValue` — a negative left/top ink overhang becomes positive
/// padding room (`v < 0 ? ceil(-v) : 0`); a cell whose ink starts at or
/// past its own origin needs no extra offset.
fn offset_value(v: f64) -> f64 {
    if v < 0.0 { (-v).ceil() } else { 0.0 }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cell(w: f64, h: f64) -> GridCell {
        GridCell::new(Bounds::new(0.0, 0.0, w, h))
    }

    /// Rows-only, single column facet (`ncols = 1`, `align: all`,
    /// `padding: 20`) — the exact shape `fixtures/facet-panel-axis-title`
    /// compiles to (probed live: `layout: {padding: 20, columns: 1,
    /// bounds: "full", align: "all"}`). Seven uniform panels, each
    /// 480x114.57142857142857 (the pre-correction unit box) — the offsets
    /// `grid_layout` returns must space them by exactly `padding +
    /// ceil(panel_height)` per row, all stacked at x=0 (one column).
    #[test]
    fn rows_only_single_column_all_alignment_stacks_uniformly() {
        let panel_h = 114.57142857142857;
        let cells: Vec<GridCell> = (0..7).map(|_| cell(480.0, panel_h)).collect();
        let opt = GridOpt { padding: 20.0, columns: 1, align_none: false };
        let (offsets, bounds) = grid_layout(&cells, &opt);
        assert_eq!(offsets.len(), 7);
        let y_max = panel_h.ceil(); // 115
        let row_step = 20.0 + y_max; // 135
        for (i, &(dx, dy)) in offsets.iter().enumerate() {
            assert_eq!(dx, 0.0, "row {i} should not shift horizontally");
            assert!((dy - i as f64 * row_step).abs() < 1e-9, "row {i}: dy={dy}, expected {}", i as f64 * row_step);
        }
        // composite height = last row's own bottom edge
        assert!((bounds.y2 - (6.0 * row_step + panel_h)).abs() < 1e-9);
        assert_eq!(bounds.x2, 480.0);
    }

    #[test]
    fn columns_only_row_of_n_positions_side_by_side() {
        let cells: Vec<GridCell> = (0..3).map(|_| cell(200.0, 100.0)).collect();
        let opt = GridOpt { padding: 20.0, columns: 3, align_none: false };
        let (offsets, bounds) = grid_layout(&cells, &opt);
        assert_eq!(offsets[0], (0.0, 0.0));
        let col_step = 20.0 + 200.0; // padding + xMax
        assert!((offsets[1].0 - col_step).abs() < 1e-9);
        assert!((offsets[2].0 - 2.0 * col_step).abs() < 1e-9);
        for &(_, dy) in &offsets {
            assert_eq!(dy, 0.0);
        }
        assert!((bounds.x2 - (2.0 * col_step + 200.0)).abs() < 1e-9);
    }

    #[test]
    fn grid_of_cells_positions_both_axes() {
        // 2x2 grid, uniform 100x50 cells, align all both directions.
        let cells: Vec<GridCell> = (0..4).map(|_| cell(100.0, 50.0)).collect();
        let opt = GridOpt { padding: 10.0, columns: 2, align_none: false };
        let (offsets, _bounds) = grid_layout(&cells, &opt);
        // row 0: (0,0), (0,10+100=110,0)... col step = padding + xMax(100) = 110
        assert_eq!(offsets[0], (0.0, 0.0));
        assert!((offsets[1].0 - 110.0).abs() < 1e-9);
        assert_eq!(offsets[1].1, 0.0);
        // row 1 starts at dy = padding + yMax(50) = 60
        assert_eq!(offsets[2].0, 0.0);
        assert!((offsets[2].1 - 60.0).abs() < 1e-9);
        assert!((offsets[3].0 - 110.0).abs() < 1e-9);
        assert!((offsets[3].1 - 60.0).abs() < 1e-9);
    }
}
