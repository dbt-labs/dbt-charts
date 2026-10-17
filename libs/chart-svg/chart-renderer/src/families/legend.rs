//! Shared vertical legend — title + swatch/label rows, used by any cartesian
//! family whose resolved `legend.direction == "vertical"` (right or bottom
//! placement; production renders both as the same vertical column, only the
//! anchor point differs — see `line.rs`'s callers).
//!
//! Geometry constants match the pre-existing right-side legend in
//! `scatter.rs`/`heatmap.rs`, measured against production reference SVGs in
//! an earlier wave: symbol radius 5px, label gap 8px, row height 16px, title
//! band 17px.

use std::fmt::Write as _;

use serde::Deserialize;

use crate::codes;
use crate::diagnostic::Diagnostic;
use crate::common::{field_display_text, truncate_label, FieldLabels, Font};
use crate::svg::{xml_escape, xml_escape_attr, vega_escape_attr};
use crate::text::FontBook;
use crate::vega_fit;

// Measured against a production reference SVG (vertical legend, line family):
// symbol drawn at radius 5 but centered in a 12px box (1px inset each side),
// row height 16, title band 17.
const LEGEND_SYMBOL_R: f64 = 5.0;
const LEGEND_SYMBOL_BOX: f64 = 12.0;
const LEGEND_LABEL_GAP: f64 = 8.0;
const LEGEND_ROW_H: f64 = 16.0;
const LEGEND_TITLE_H: f64 = 17.0;
/// Gap between the plot's own content (axis labels, rail) and the legend panel.
pub const LEGEND_GAP: f64 = 24.0;

#[derive(Deserialize)]
pub struct LegendConfig {
    pub visible: bool,
    #[serde(deserialize_with = "legend_position")]
    pub position: LegendPosition,
    pub direction: String,
    /// The resolver's compact-mode wrap (`_kwargs.py`: `compact_columns`
    /// when the card is too narrow for one row, else 0 — one row).
    #[serde(default)]
    pub columns: Option<u32>,
    pub label: LegendLabel,
    pub title: LegendTitle,
    /// Authored `style.legend.values`. A grouped bar's legend domain uses
    /// it (resolved: an exact spelling, or a case/`-`/`_`/space-folded one)
    /// when it's a FULL reordering of the chart's own color domain (same
    /// set, different order -- a curated subset is a legend-only filter,
    /// not a usable domain) -- see `grouped_legend_domain` in `bar.rs`,
    /// mirroring `emitters/_channels.py`'s `resolve_legend_entries` +
    /// `bar.py`'s own gate for this branch.
    #[serde(default)]
    pub values: Option<Vec<String>>,
    /// VL `symbolLimit` (`legend_to_vl`'s `symbol_limit`): the most entries a
    /// symbol legend draws; Vega folds the rest into an "…N entries" row.
    #[serde(default)]
    pub symbol_limit: Option<u32>,
    /// Authored `legend.symbol_shape` (VL `symbolType`).
    #[serde(default)]
    pub symbol_shape: Option<String>,
    /// Authored `legend.symbol_fill: false` (VL `symbolFillColor: transparent`).
    #[serde(default)]
    pub symbol_fill: Option<bool>,
}

/// Where along its edge a reserved legend strip sits. `End` is a pie key's
/// alone (`PiePositionStyle` takes `start|center|end`; a cartesian strip
/// refuses it, `refuse_unmodeled_slide`).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum LegendAlign {
    Start,
    Center,
    End,
}

/// The resolved placement (`ResolvedLegendPosition`) of a legend: a strip
/// reserved on `edge`, or (`overlay`) a box floating inside the plot at that
/// edge's corner (`legend_overlay_origin`).
#[derive(Debug)]
pub struct LegendPosition {
    pub edge: String,
    pub align: LegendAlign,
    pub overlay: bool,
}

/// Deserializes `ResolvedLegendPosition`. A floating legend on a side edge
/// is centered or nothing (`validate_cartesian_position`,
/// `compile/resolve/style/legend_position.py`); a pie never floats
/// (`decide_legend_position`'s `radial`).
pub(crate) fn legend_position<'de, D: serde::Deserializer<'de>>(d: D) -> Result<LegendPosition, D::Error> {
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Raw {
        edge: String,
        align: String,
        overlay: bool,
    }
    let raw = Raw::deserialize(d)?;
    let align = match raw.align.as_str() {
        "start" => LegendAlign::Start,
        "center" => LegendAlign::Center,
        "end" => LegendAlign::End,
        other => return Err(serde::de::Error::custom(format!("legend.position align {other:?} is not a legend align"))),
    };
    // `LegendEdge` (`compile/models/style/theme/legend.py:21`) is the four
    // edges; `legend_origin` relies on this check for its exhaustive match.
    if !matches!(raw.edge.as_str(), "left" | "right" | "top" | "bottom") {
        return Err(serde::de::Error::custom(format!("legend.position edge {:?} is not a legend edge", raw.edge)));
    }
    if raw.overlay && matches!(raw.edge.as_str(), "left" | "right") && align != LegendAlign::Center {
        return Err(serde::de::Error::custom(format!(
            "legend.position edge {:?} align {:?} overlay true: a floating side legend is centered",
            raw.edge, raw.align
        )));
    }
    Ok(LegendPosition { edge: raw.edge, align, overlay: raw.overlay })
}

/// A centered legend reserves the same strip as a start-aligned one and then
/// slides along its edge (`render/converters/legend_align.py:68-103`). Small
/// multiples have no single plot to slide it along
/// (`ERR-LEGEND-ALIGN-UNSUPPORTED`, raised from the payload by
/// `cartesian_errors`); line, area, bar, heatmap and scatter draw the slide on
/// their own arms.
///
/// A reserved cartesian strip supports `align: start|center` only: `end` is a
/// floating-corner placement (a floating legend takes it as its corner) and
/// production's resolve raises `ERR-LEGEND-POSITION-UNSUPPORTED` for a strip
/// with it (`validate_cartesian_position`,
/// `compile/resolve/style/legend_position.py:110-128`), so the families that
/// draw a cartesian legend refuse it. A pie's own `PiePositionStyle` never
/// overlays, so its `end` is a reserved strip slid to the far end
/// (`legend_align.py:100-103`); a family that draws no legend (table, kpi) is
/// `legendless` in the resolver, which leaves a board-wide `end` default
/// unvalidated on its payload, and production renders it.
pub fn refuse_unmodeled_slide(chart_type: &str, resolved: &serde_json::Value) -> Result<(), String> {
    let align = &resolved["legend"]["position"]["align"];
    let overlay = resolved["legend"]["position"]["overlay"] == true;
    if align == "end" && !overlay && matches!(chart_type, "line" | "area" | "bar" | "histogram" | "heatmap" | "scatter") {
        return Err(format!(
            "ERR-LEGEND-POSITION-UNSUPPORTED: {chart_type}: legend.position align \"end\" overlay false — \
             a reserved strip supports align \"start\" or \"center\" only"
        ));
    }
    Ok(())
}

/// Refuses a centered legend by name when `blocker` is set: the arm a
/// family draws it on has no single plot to slide it along, or is not probed
/// against production. `blocker` is `None` for a start-aligned legend, a
/// hidden one, and every arm that draws the slide.
pub fn refuse_slide_blocker(position: &LegendPosition, family: &str, blocker: Option<&str>) -> Result<(), String> {
    match blocker {
        Some(blocker) if position.align != LegendAlign::Start => {
            Err(format!("{family}: legend.position align \"center\" {blocker}"))
        }
        _ => Ok(()),
    }
}

/// How far a legend slides along its edge: `(dx, dy)` from its start-anchored
/// origin. A centered legend's block moves to `(span − block)/2`, `span` the
/// plot's own extent along the edge (width on a top or bottom edge, height on
/// a side) and `block` the legend's (`legend_align.py:93-103`); a reserved
/// strip's block starts at the plot's own origin (0), a floating one at its
/// corner. A legend no smaller than its plot has nowhere to slide
/// (`legend_align.py:84-97`).
pub fn legend_slide(
    chart_id: &str,
    position: &LegendPosition,
    legend: &VegaLegend,
    plot: (f64, f64),
) -> Result<(f64, f64), Diagnostic> {
    slide_block(chart_id, position, (legend.box_w, legend.box_h()), plot)
}

/// A floating legend starts at its corner (`legend_overlay_origin`), and a
/// corner is already end-aligned, so only a centered one slides
/// (`vega_lite.py`: `needs_slide`); the slide reads the block's own start the
/// way `legend_align.py` reads it off the scenegraph.
pub fn slide_block(
    chart_id: &str,
    position: &LegendPosition,
    block: (f64, f64),
    plot: (f64, f64),
) -> Result<(f64, f64), Diagnostic> {
    let start = if position.overlay { legend_overlay_origin(position, plot, block) } else { (0.0, 0.0) };
    slide_union(chart_id, position, &[(start.0, start.1, block.0, block.1)], plot)
}

/// The slide of every legend in one view as a block (`legend_shifts`,
/// `legend_align.py:80-105`): `rects` are the legends' `(x, y, w, h)` in plot
/// coordinates, the block runs from the first start to the last end along
/// the edge, and one shift moves them all so stacked legends keep their
/// order. A start-aligned legend, and a floating one in its end corner,
/// stay put.
pub fn slide_union(
    chart_id: &str,
    position: &LegendPosition,
    rects: &[(f64, f64, f64, f64)],
    plot: (f64, f64),
) -> Result<(f64, f64), Diagnostic> {
    let unsupported = |reason: String| {
        Diagnostic::new(
            &codes::ERR_LEGEND_ALIGN_UNSUPPORTED,
            [("chart_id", serde_json::json!(chart_id)), ("reason", serde_json::json!(reason))],
        )
    };
    if position.align == LegendAlign::Start
        || (position.overlay && position.align == LegendAlign::End)
        || rects.is_empty()
    {
        return Ok((0.0, 0.0));
    }
    let along_x = matches!(position.edge.as_str(), "top" | "bottom");
    let span = if along_x { plot.0 } else { plot.1 };
    let (block_start, block_end) = rects.iter().fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), &(x, y, w, h)| {
        let (start, size) = if along_x { (x, w) } else { (y, h) };
        (lo.min(start), hi.max(start + size))
    });
    // `legend_align.py:89-93`: an empty span refuses before the block is compared.
    if span <= 0.0 {
        return Err(unsupported("its plot has no size to align in".to_string()));
    }
    let block = block_end - block_start;
    if block >= span {
        // `legend_align.py:98` prints both extents through `:g`.
        return Err(unsupported(format!(
            "its legend ({}px) is larger than its plot ({}px)",
            d3rs::py_float::format_g(block),
            d3rs::py_float::format_g(span),
        )));
    }
    // `legend_align.py:100-103`: center is `(span - block) / 2 - start` and
    // end `span - end`.
    let shift = match position.align {
        LegendAlign::Center => (span - block) / 2.0 - block_start,
        LegendAlign::End => span - block_end,
        LegendAlign::Start => unreachable!("a start-aligned legend returned above"),
    };
    Ok(if along_x { (shift, 0.0) } else { (0.0, shift) })
}

/// Case/separator-fold a legend token for alias matching: collapse `-`, `_`
/// and runs of whitespace to a single space, then lowercase, so
/// `net_revenue`, `Net-Revenue` and `net revenue` all fold to the same key.
///
/// ASCII-equivalent to `emitters/_channels.py`'s `_fold_legend_token`, not
/// identical: that folds with `casefold()`, this with `to_lowercase()`, so
/// they disagree where casefold expands a character: `straße` folds to
/// `strasse` in Python and stays `straße` here.
pub fn fold_legend_token(token: &str) -> String {
    let mut out = String::with_capacity(token.len());
    let mut last_was_sep = false;
    for c in token.chars() {
        if c == '-' || c == '_' || c.is_whitespace() {
            if !last_was_sep {
                out.push(' ');
            }
            last_was_sep = true;
        } else {
            out.push(c);
            last_was_sep = false;
        }
    }
    out.trim().to_lowercase()
}

/// Production's `apply_legend_entry_order` for an authored `legend.values`
/// (`emitters/_channels.py`: `resolve_legend_entries`, then the dedupe and the
/// nothing-matched fallback): indices into `domain` in authored order. A
/// token takes the domain entry it spells exactly, else the one entry whose
/// alias it spells exactly, else the one entry it folds to
/// (`fold_legend_token`); a token matching nothing, or more than one entry at
/// its tier, is dropped, never guessed. When no token resolves the whole
/// domain keeps its own order. `authored` is never empty here (an explicit
/// `values: []` draws a title-only legend, which callers handle before this).
///
/// `aliases`, when given, is one extra spelling per `domain` entry (a wide
/// fold's raw measure name for its humanized label, `wide_legend_aliases`);
/// an alias spelling another entry's own name never resolves to this one.
pub fn resolve_legend_values(
    authored: &[String],
    domain: &[&str],
    aliases: Option<&[&str]>,
) -> Vec<usize> {
    let alias_of = |idx: usize| aliases.map(|spellings| spellings[idx]);
    let resolve = |token: &str| -> Option<usize> {
        if let Some(idx) = domain.iter().position(|entry| *entry == token) {
            return Some(idx);
        }
        let mut aliased = (0..domain.len()).filter(|&idx| alias_of(idx) == Some(token));
        if let (Some(idx), None) = (aliased.next(), aliased.next()) {
            return Some(idx);
        }
        let folded = fold_legend_token(token);
        let mut matches = (0..domain.len()).filter(|&idx| {
            fold_legend_token(domain[idx]) == folded
                || alias_of(idx).is_some_and(|alias| {
                    !domain.contains(&alias) && fold_legend_token(alias) == folded
                })
        });
        match (matches.next(), matches.next()) {
            (Some(idx), None) => Some(idx),
            _ => None,
        }
    };
    let mut resolved: Vec<usize> = Vec::new();
    for token in authored {
        if let Some(idx) = resolve(token).filter(|idx| !resolved.contains(idx)) {
            resolved.push(idx);
        }
    }
    if resolved.is_empty() {
        (0..domain.len()).collect()
    } else {
        resolved
    }
}

#[derive(Deserialize)]
pub struct LegendLabel {
    pub font: Font,
    /// VL `labelOffset`: the gap from the entry's symbol cell to its label.
    #[serde(default)]
    pub padding: Option<f64>,
    /// VL `labelLimit` (`legend_to_vl`'s `label.max_width`): Vega truncates a
    /// wider label with an ellipsis. Read by pie, the one family that draws
    /// long category names unwrapped in a legend.
    #[serde(default)]
    pub max_width: Option<f64>,
}

/// Vega's symbol-legend entry grid, decoded from the bundled vega source
/// (vega-parser's legend groups and vega-view-transforms' `gridLayout`).
///
/// The parser lays a symbol legend's entries out as a grid with
/// `{align: each, columns, center: {row: true, column: false}, padding:
/// {row: rowPadding, column: columnPadding}}` and gives every entry the
/// same size signal, `max(ceil(sqrt(symbolSize) + symbolStrokeWidth),
/// labelFontSize)`: the symbol is centered at `size/2` in both axes and the
/// label sits at `x = size + labelOffset`, baseline middle at `size/2`.
/// With Vega's defaults (symbolSize 100, symbolStrokeWidth 1.5,
/// rowPadding 2, columnPadding 10) an 11px legend has size 12: symbol
/// bounds −0.5…12.5 (boundStroke expands by the full stroke width), label
/// bounds 0…11.
///
/// **Mixed-mark exception** (`entries` carrying a `Stroke` glyph —
/// `_mixed_mark_legend_symbols`, `_overlay.py:679-687`): binding
/// `symbolSize`/`symbolStrokeWidth` to a `datum`-conditional signal breaks
/// Vega's own `sizeExpression` (`vega-parser/legend.js:176-183`), which
/// runs once as a transform PARAM with no `datum` in scope — probed via
/// `vega_to_scenegraph` on the compiled `dual-y-axis-lab/
/// revenue_vs_conversion` spec: every entry's cell collapses to
/// `fontSize` alone (`c = fontSize/2`, not `max(ceil(sqrt(size)+
/// strokeWidth), fontSize)`), while each entry's OWN ink still follows
/// its REAL shape — a square/circle keeps the fixed 10-wide/r5 glyph plus
/// its 1.5 stroke (`emit_vega_legend`'s own hardcoded draw), a
/// `stroke` glyph's VERTICAL ink is just half its OWN stroke width (a
/// horizontal line has no radius; its horizontal reach is the 20px
/// segment's half-length plus the full stroke width — `half_extent`'s
/// per-axis split below). Probed: entry0 (square) reports scenegraph height
/// 12, entry1 (stroke, width 2) reports 10.5, both centered at the SAME
/// `c = 5.5`.
///
/// `gridLayout` then, per item with bounds relative to its origin: a
/// column's pitch is `columnPadding + ceil(−x1) + ceil(max x2 in the
/// previous column)`, a row's pitch `rowPadding + ceil(−y1) + ceil(max y2
/// in the previous row)`; and `center.row` adds `(ceil(y2) − y2)/2` to an
/// item — but its test subtracts the item's accumulated row offset, so
/// only the FIRST row is ever centered (0.25 at 11px; rows at 0.25, 16,
/// 32, …), and not at all in a one-column grid (`1 !== v` in that test). The legend's height is the ceil'd bottom edge of the last row
/// (`row_y + y2`) — ink above the origin does not count (29 at 11px, 34
/// at 15px). Offsets and height probe-pinned across stroke widths 0.5–3
/// and font sizes 11–16px, 120 of 120 configurations.
pub struct LegendGrid {
    /// The entry size signal — the symbol cell's side.
    pub size: f64,
    /// The label's x within its cell: `size + labelOffset`.
    pub label_x: f64,
    pub col_x: Vec<f64>,
    pub row_y: Vec<f64>,
    pub box_h: f64,
    /// The entries' ink width: the widest `col_x + x2`, unceiled.
    pub width: f64,
}

const VEGA_SYMBOL_SIZE: f64 = 100.0;
const VEGA_DASH_SYMBOL_SIZE: f64 = 2000.0;
const VEGA_SYMBOL_STROKE_WIDTH: f64 = 1.5;
const VEGA_ROW_PADDING: f64 = 2.0;
const VEGA_COLUMN_PADDING: f64 = 10.0;

/// Lay `label_ws` (one per entry, in legend order) out at `font_size` in
/// `columns` columns (0 → one row). `label_offset` is VL's `labelOffset`.
/// `symbols[i]` is `Some(..)` only for a mixed-mark legend entry (see the
/// struct doc's mixed-mark exception) — `&[None; n]` for every ordinary
/// (uniform) legend.
pub fn legend_grid(
    label_ws: &[f64],
    symbols: &[Option<LegendSymbol>],
    font_size: f64,
    columns: usize,
    label_offset: f64,
) -> LegendGrid {
    let n = label_ws.len();
    if n == 0 {
        return LegendGrid { size: 0.0, label_x: 0.0, col_x: Vec::new(), row_y: Vec::new(), box_h: 0.0, width: 0.0 };
    }
    let columns = if columns == 0 { n } else { columns.min(n) };
    let rows = n.div_ceil(columns);
    // `_mixed_mark_legend_symbols`: a stroke-family glyph collapses the cell to
    // the font size; entries drawing only Vega's default 100-size square or
    // circle keep the uniform row.
    let mixed = symbols
        .iter()
        .any(|s| matches!(s, Some(LegendSymbol::Stroke(_) | LegendSymbol::StrokedCircle(_))));
    let size = if symbols.iter().any(|s| matches!(s, Some(LegendSymbol::Dash(_)))) {
        (VEGA_DASH_SYMBOL_SIZE.sqrt() + VEGA_SYMBOL_STROKE_WIDTH).ceil().max(font_size)
    } else if mixed {
        font_size
    } else {
        (VEGA_SYMBOL_SIZE.sqrt() + VEGA_SYMBOL_STROKE_WIDTH).ceil().max(font_size)
    };
    let c = size / 2.0;
    // Each entry's own half-extent beyond `c`, per axis: a square/circle
    // keeps the fixed 10-wide/r5 glyph (`emit_vega_legend`'s hardcoded
    // draw) plus its 1.5 stroke on both axes, same as the uniform law. A
    // `stroke` glyph is the fixed 20px segment `M-10,0L10,0` (`docs/
    // laws-layers.md`'s mixed-mark law) — Vega's `boundStroke` expands its
    // drawn bound by the FULL stroke width, so its horizontal half is
    // `10 + strokeWidth`; vertically the segment itself has no extent, so
    // its half is just `strokeWidth / 2`.
    let stroke_half_extent = |half_len: f64, w: f64| (half_len + w, w / 2.0);
    let half_extent_x = |i: usize| match symbols.get(i).copied().flatten() {
        Some(LegendSymbol::Glyph(x, _) | LegendSymbol::Ring(.., (x, _))) => x + VEGA_SYMBOL_STROKE_WIDTH,
        Some(LegendSymbol::Stroke(w)) => stroke_half_extent(10.0, w).0,
        Some(LegendSymbol::ShortStroke) => stroke_half_extent(5.0, VEGA_SYMBOL_STROKE_WIDTH).0,
        Some(LegendSymbol::Dash(_)) => VEGA_DASH_SYMBOL_SIZE.sqrt() / 2.0 + VEGA_SYMBOL_STROKE_WIDTH,
        Some(LegendSymbol::FilledCircle | LegendSymbol::FilledGlyph(_) | LegendSymbol::FilledSquare) => {
            VEGA_SYMBOL_SIZE.sqrt() / 2.0
        }
        _ => VEGA_SYMBOL_SIZE.sqrt() / 2.0 + VEGA_SYMBOL_STROKE_WIDTH,
    };
    let half_extent_y = |i: usize| match symbols.get(i).copied().flatten() {
        Some(LegendSymbol::Glyph(_, y) | LegendSymbol::Ring(.., (_, y))) => y + VEGA_SYMBOL_STROKE_WIDTH,
        Some(LegendSymbol::Stroke(w)) => stroke_half_extent(10.0, w).1,
        Some(LegendSymbol::ShortStroke) => stroke_half_extent(5.0, VEGA_SYMBOL_STROKE_WIDTH).1,
        Some(LegendSymbol::Dash(_)) => VEGA_SYMBOL_STROKE_WIDTH / 2.0,
        Some(LegendSymbol::FilledCircle | LegendSymbol::FilledGlyph(_) | LegendSymbol::FilledSquare) => {
            VEGA_SYMBOL_SIZE.sqrt() / 2.0
        }
        _ => VEGA_SYMBOL_SIZE.sqrt() / 2.0 + VEGA_SYMBOL_STROKE_WIDTH,
    };
    let text_y1 = c + (0.3 * font_size).round() - (0.8 * font_size).round();
    let y1 = |i: usize| (c - half_extent_y(i)).min(text_y1);
    let y2 = |i: usize| (c + half_extent_y(i)).max(text_y1 + font_size);
    let x2 = |i: usize| size + label_offset + label_ws[i];
    let neg = |v: f64| if v < 0.0 { (-v).ceil() } else { 0.0 };
    // `x1` of the column being PLACED (Vega's `gridLayout` takes it from
    // the column, not a fixed entry) — the min over that column's own
    // entries, mirroring `row_y1`'s per-row aggregation below.
    let col_x1 = |col: usize| {
        (0..n).filter(|i| i % columns == col).map(|i| c - half_extent_x(i)).fold(f64::INFINITY, f64::min)
    };
    let mut col_x = vec![0.0; columns];
    for col in 1..columns {
        let prev_w = (0..n).filter(|i| i % columns == col - 1).map(|i| x2(i).ceil()).fold(0.0, f64::max);
        col_x[col] = col_x[col - 1] + VEGA_COLUMN_PADDING + neg(col_x1(col)) + prev_w;
    }
    // Row aggregates (min y1, max y2 over that row's own entries) — for a
    // uniform legend every entry shares one (y1, y2), so this reduces to
    // the prior single-value formula exactly.
    let row_y1 = |r: usize| (0..n).filter(|i| i / columns == r).map(y1).fold(f64::INFINITY, f64::min);
    let row_y2 = |r: usize| (0..n).filter(|i| i / columns == r).map(y2).fold(f64::NEG_INFINITY, f64::max);
    let first = if columns == 1 { 0.0 } else { (row_y2(0).ceil() - row_y2(0)) / 2.0 };
    // Row 0's `first` centering nudge does NOT propagate into later rows'
    // own cumulative pitch (`gridLayout`'s `center.row` step only ever
    // touches row 0 — the doc comment above's "only the FIRST row is ever
    // centered") — accumulate the row PITCH chain separately from `first`.
    let mut row_y = vec![0.0; rows];
    row_y[0] = first;
    let mut cumulative = 0.0;
    for (r, slot) in row_y.iter_mut().enumerate().skip(1) {
        cumulative += VEGA_ROW_PADDING + neg(row_y1(r)) + row_y2(r - 1).ceil();
        *slot = cumulative;
    }
    let box_h = (row_y[rows - 1] + row_y2(rows - 1)).ceil();
    let width = (0..n).map(|i| col_x[i % columns] + x2(i)).fold(0.0, f64::max);
    LegendGrid { size, label_x: size + label_offset, col_x, row_y, box_h, width }
}

/// A size (or other continuous symbol-size-driven) legend's per-entry grid:
/// unlike [`legend_grid`]'s uniform-symbol grid (every entry the same
/// `symbolSize`), a size legend's `LegendEntries` carries a DIFFERENT
/// symbol size per row (the scale's own value at that tick), so each row's
/// own vertical extent follows ITS symbol — a 156-max `open_pipeline`
/// domain's row pitch grows 14, 14, 16, 18, 20, 21, 23 px as the symbols
/// grow. The horizontal position stays uniform down the column (`gridLayout`
/// aligns a column's cells by ITS widest member, exactly the width side of
/// the uniform-grid law above): `symbol_c`/`label_x` use the LARGEST
/// entry's own cell, so a small entry's symbol still centers under a big
/// one's and every label starts at the same x. `sizes` and `label_ws` are
/// one entry per row, in row order (bottom of the domain first, as Vega's
/// own `LegendEntries` emits); always one column — a continuous legend
/// never wraps (probe: `gridLayout`'s `1 !== v` first-row-center test is
/// moot here, so row 0 always starts at 0, no half-pixel centering term).
pub struct SizeLegendGrid {
    /// Uniform x offset of every row's symbol center — the LARGEST entry's
    /// own cell half.
    pub symbol_c: f64,
    /// Uniform label x — `2 * symbol_c + label_offset`.
    pub label_x: f64,
    /// Per-row OWN vertical symbol/label center (`cell_i / 2`) — read by
    /// the caller for both the symbol's `cy` and the label's `y`.
    pub row_c: Vec<f64>,
    pub row_y: Vec<f64>,
    pub box_h: f64,
    /// The entries' ink width, unceiled — mirrors [`LegendGrid::width`].
    pub width: f64,
}

/// One entry of a size legend in its legend's frame: the entry cell's top
/// `y`, the symbol center's `(symbol_x, c)` and the label's `label_x`
/// (the label shares the symbol's vertical center `y + c`).
pub struct SizeCell {
    pub y: f64,
    pub c: f64,
    pub symbol_x: f64,
    pub label_x: f64,
}

/// A size legend laid out as one horizontal row, one column per entry
/// (`direction: horizontal`): each entry's cell is its own symbol size, the
/// columns `gridLayout`'s `columnPadding` (10) past the last column's ceiled
/// right bound and any negative left bound of their own, and every entry
/// vertically centered in the row (whose height is its lowest entry bottom, ceiled) — a probe of Vega's `gridLayout` on production's size legend.
pub fn size_legend_row(
    sizes: &[f64],
    label_ws: &[f64],
    font_size: f64,
    label_offset: f64,
    glyph: (f64, f64),
) -> (Vec<SizeCell>, f64, f64) {
    let n = sizes.len();
    let cell = |size: f64| (size.sqrt() + VEGA_SYMBOL_STROKE_WIDTH).ceil().max(font_size);
    let c: Vec<f64> = sizes.iter().map(|&s| cell(s) / 2.0).collect();
    let half_x: Vec<f64> = sizes.iter().map(|&s| s.sqrt() / 2.0 * glyph.0).collect();
    let half_y: Vec<f64> = sizes.iter().map(|&s| s.sqrt() / 2.0 * glyph.1).collect();
    let text_y1 = |c: f64| c + (0.3 * font_size).round() - (0.8 * font_size).round();
    let x1: Vec<f64> = (0..n).map(|i| c[i] - half_x[i] - VEGA_SYMBOL_STROKE_WIDTH).collect();
    let label_x: Vec<f64> = (0..n).map(|i| 2.0 * c[i] + label_offset).collect();
    let x2: Vec<f64> = (0..n).map(|i| (c[i] + half_x[i] + VEGA_SYMBOL_STROKE_WIDTH).max(label_x[i] + label_ws[i])).collect();
    let y2: Vec<f64> = (0..n)
        .map(|i| (c[i] + half_y[i] + VEGA_SYMBOL_STROKE_WIDTH).max(text_y1(c[i]) + font_size))
        .collect();
    let neg = |v: f64| if v < 0.0 { (-v).ceil() } else { 0.0 };
    let mut col_x = vec![0.0; n];
    for i in 1..n {
        col_x[i] = col_x[i - 1] + VEGA_COLUMN_PADDING + neg(x1[i]) + x2[i - 1].ceil();
    }
    // The row is as tall as its lowest entry's bottom: an entry group's top
    // edge is its own origin, so ink above it adds no height.
    let box_h = y2.iter().copied().fold(0.0, f64::max).ceil();
    let cells = (0..n)
        .map(|i| SizeCell { y: (box_h - y2[i]) / 2.0, c: c[i], symbol_x: col_x[i] + c[i], label_x: col_x[i] + label_x[i] })
        .collect();
    let width = (0..n).map(|i| col_x[i] + x2[i]).fold(0.0, f64::max);
    (cells, box_h, width)
}

/// Lay `sizes` (Vega scale values, one per legend row, row order) out under
/// `label_ws` (matching label widths) at `font_size`, `label_offset` apart
/// from the symbol column — Vega's `gridLayout` applied to a size legend's
/// per-row bounds (see [`SizeLegendGrid`]).
pub fn size_legend_grid(
    sizes: &[f64],
    label_ws: &[f64],
    font_size: f64,
    label_offset: f64,
    glyph: (f64, f64),
) -> SizeLegendGrid {
    let n = sizes.len();
    if n == 0 {
        return SizeLegendGrid {
            symbol_c: 0.0,
            label_x: 0.0,
            row_c: Vec::new(),
            row_y: Vec::new(),
            box_h: 0.0,
            width: 0.0,
        };
    }
    let cell = |size: f64| {
        (size.sqrt() + VEGA_SYMBOL_STROKE_WIDTH).ceil().max(font_size)
    };
    let row_c: Vec<f64> = sizes.iter().map(|&s| cell(s) / 2.0).collect();
    let half: Vec<f64> = sizes.iter().map(|&s| s.sqrt() / 2.0 * glyph.1).collect();
    let text_y1 = |c: f64| c + (0.3 * font_size).round() - (0.8 * font_size).round();
    let y1: Vec<f64> = (0..n)
        .map(|i| (row_c[i] - half[i] - VEGA_SYMBOL_STROKE_WIDTH).min(text_y1(row_c[i])))
        .collect();
    let y2: Vec<f64> = (0..n)
        .map(|i| (row_c[i] + half[i] + VEGA_SYMBOL_STROKE_WIDTH).max(text_y1(row_c[i]) + font_size))
        .collect();
    let neg = |v: f64| if v < 0.0 { (-v).ceil() } else { 0.0 };
    let mut row_y = vec![0.0; n];
    for i in 1..n {
        row_y[i] = row_y[i - 1] + VEGA_ROW_PADDING + neg(y1[i]) + y2[i - 1].ceil();
    }
    let box_h = (row_y[n - 1] + y2[n - 1]).ceil();
    let symbol_c = row_c.iter().cloned().fold(0.0, f64::max);
    let label_x = 2.0 * symbol_c + label_offset;
    let width = (0..n).map(|i| label_x + label_ws[i]).fold(0.0, f64::max);
    SizeLegendGrid { symbol_c, label_x, row_c, row_y, box_h, width }
}

/// Vega's default legend `titlePadding`; production forwards none.
/// Shared with heatmap's gradient legend, whose title band follows the
/// same `titleFontSize + titlePadding` rule.
pub const VEGA_LEGEND_TITLE_PADDING: f64 = 5.0;

/// Vega-Lite's `gradientThickness` default: the color bar's width.
pub(crate) const VEGA_GRADIENT_THICKNESS: f64 = 16.0;
/// Vega-Lite's `gradientLength` default is `clamp(plot_height, 64, 200)`
/// (probed: spec heights 700 / 200 / 140 give bars of 200 / 116 / 64).
pub(crate) const VEGA_GRADIENT_LENGTH_MIN: f64 = 64.0;
pub(crate) const VEGA_GRADIENT_LENGTH_MAX: f64 = 200.0;
/// A horizontal `orient: top` gradient legend is `clamp(unit width, 100, 200)`
/// long: Vega-Lite `defaultGradientLength` (`src/compile/legend/properties.ts`,
/// v6.4 lines 206-243) reads `model.getSizeSignalRef('width')`
/// (`src/compile/model.ts:493`), which for a unit inside a facet is the
/// unit's own `child_width`, and the bounds are `legendConfig.gradientHorizontal
/// {Min,Max}Length` (`src/legend.ts:194-195`).
const VEGA_GRADIENT_HORIZONTAL_MIN_LENGTH: f64 = 100.0;
/// How many ticks color a continuous legend's gradient between its two ends.
pub(crate) const GRADIENT_STOP_TICKS: usize = 15;

/// `value`'s place in `[min, max]` as d3's `normalize` puts it: a zero-width
/// domain maps everything to the middle.
pub(crate) fn unit_position(value: f64, min: f64, max: f64) -> f64 {
    if max == min {
        0.5
    } else {
        (value - min) / (max - min)
    }
}

/// A vertical gradient legend's bar length beside a plot `plot_h` tall.
pub(crate) fn gradient_length(plot_h: f64) -> f64 {
    plot_h.clamp(VEGA_GRADIENT_LENGTH_MIN, VEGA_GRADIENT_LENGTH_MAX)
}

/// A horizontal gradient legend's length on `edge`: the same
/// `defaultGradientLength` gives a top or bottom strip the clamped unit width
/// and a side strip or a floating (corner orient) one
/// `gradientHorizontalMinLength` alone.
pub(crate) fn horizontal_gradient_length(edge: &str, overlay: bool, plot_w: f64) -> f64 {
    if !overlay && matches!(edge, "top" | "bottom") {
        plot_w.clamp(VEGA_GRADIENT_HORIZONTAL_MIN_LENGTH, VEGA_GRADIENT_LENGTH_MAX)
    } else {
        VEGA_GRADIENT_HORIZONTAL_MIN_LENGTH
    }
}

/// How many ticks label a gradient `length` px long.
pub(crate) fn gradient_tick_count(length: f64) -> usize {
    ((length / 100.0).floor() as usize * 2).max(2)
}

/// A gradient legend's labeled values: the scale's ticks, or its two ends when
/// there are fewer than three (`LegendEntries`), a one-value domain's single
/// label drawn once (the legend's data join keys its entries by value).
pub(crate) fn gradient_labeled_values(ticks: Vec<f64>, min: f64, max: f64) -> Vec<f64> {
    match (ticks.len() < 3, min == max) {
        (false, _) => ticks,
        (true, true) => vec![min],
        (true, false) => vec![min, max],
    }
}

/// A vertical gradient legend's box: `ceil(max(title, bar + offset + widest
/// label))` wide (`label_w` is `None` with no label to offset from the bar),
/// under a title band when titled.
pub(crate) fn gradient_legend_box(
    title_w: f64,
    title_size: f64,
    label_w: Option<f64>,
    label_offset: f64,
    length: f64,
) -> (f64, f64) {
    let band = if title_w > 0.0 { title_size + VEGA_LEGEND_TITLE_PADDING } else { 0.0 };
    let bar_w = VEGA_GRADIENT_THICKNESS + label_w.map_or(0.0, |w| label_offset + w);
    (title_w.max(bar_w).ceil(), band + length)
}

/// The `<linearGradient>` of a continuous legend: a stop at each end and at
/// each of the scale's 15 ticks, each in the scale's own color.
pub(crate) fn emit_scale_gradient_def(
    out: &mut String,
    id: &str,
    (min, max): (f64, f64),
    ticks: &[f64],
    color: impl Fn(f64) -> String,
    vertical: bool,
) {
    let axis = if vertical { "x1=\"0\" x2=\"0\" y1=\"1\" y2=\"0\"" } else { "x1=\"0\" x2=\"1\" y1=\"0\" y2=\"0\"" };
    let _ = write!(out, "<defs><linearGradient id=\"{id}\" {axis}>", id = xml_escape_attr(id));
    let lead = ticks.first().is_none_or(|&t| t != min).then_some(min);
    let tail = ticks.last().is_none_or(|&t| t != max).then_some(max);
    for v in lead.into_iter().chain(ticks.iter().copied()).chain(tail) {
        let _ = write!(out, "<stop offset=\"{}\" stop-color=\"{}\"/>", (v - min) / (max - min), color(v));
    }
    out.push_str("</linearGradient></defs>");
}

/// The `<linearGradient>` of a legend over a one-value domain, which has no
/// ticks to sample: Vega runs its whole ramp across the bar in 20 even steps.
pub(crate) fn emit_ramp_gradient_def(out: &mut String, id: &str, vertical: bool, color_at: impl Fn(f64) -> String) {
    let axis = if vertical { "x1=\"0\" x2=\"0\" y1=\"1\" y2=\"0\"" } else { "x1=\"0\" x2=\"1\" y1=\"0\" y2=\"0\"" };
    let _ = write!(out, "<defs><linearGradient id=\"{id}\" {axis}>", id = xml_escape_attr(id));
    for step in 0..=20 {
        let t = f64::from(step) / 20.0;
        let _ = write!(out, "<stop offset=\"{t}\" stop-color=\"{}\"/>", color_at(t));
    }
    out.push_str("</linearGradient></defs>");
}

/// A vertical gradient legend: title, color bar (min at the bottom), and the
/// labeled values beside it.
pub(crate) struct VerticalGradient<'a> {
    pub gradient_id: &'a str,
    pub title: &'a str,
    pub range: (f64, f64),
    pub values: &'a [f64],
    pub labels: &'a [String],
    pub length: f64,
}

impl VerticalGradient<'_> {
    /// Paint into `svg` with the group's top-left at `(x, y)`.
    pub(crate) fn paint(&self, svg: &mut String, cfg: &LegendConfig, x: f64, y: f64) -> Result<(), String> {
        let label_font = &cfg.label.font;
        let label_size = label_font.size_or(11.0);
        let label_family = label_font.family_or("sans-serif");
        let label_offset = cfg.label.padding.ok_or("legend.label.padding absent (theme required)")?;
        let title_size = cfg.title.font.size_or(12.0);
        let mut bar_y = y;
        if !self.title.is_empty() {
            let _ = write!(
                svg,
                "<text x=\"{x:.2}\" y=\"{:.2}\" text-anchor=\"start\" font-family=\"{}\" \
                 font-size=\"{title_size}\" font-weight=\"{}\" fill=\"{}\">{}</text>",
                y + vega_fit::baseline_shift("top", title_size),
                vega_escape_attr(cfg.title.font.family_or("sans-serif")),
                xml_escape_attr(&cfg.title.font.weight_str()),
                xml_escape_attr(cfg.title.font.color.as_deref().unwrap_or("#313233")),
                xml_escape(self.title),
            );
            bar_y += title_size + VEGA_LEGEND_TITLE_PADDING;
        }
        let _ = write!(
            svg,
            "<rect x=\"{x:.2}\" y=\"{bar_y:.2}\" width=\"{VEGA_GRADIENT_THICKNESS}\" height=\"{}\" fill=\"url(#{})\"/>",
            self.length,
            xml_escape_attr(self.gradient_id),
        );
        let label_color = label_font.color.as_deref().unwrap_or("#626366");
        let label_weight = label_font.weight_str();
        let label_x = x + VEGA_GRADIENT_THICKNESS + label_offset;
        let (min, max) = self.range;
        crate::contract::legend_label_open(svg, "");
        for (v, text) in self.values.iter().zip(self.labels) {
            let t = unit_position(*v, min, max);
            // A label at the bar's bottom end tucks inside as `bottom`, one at
            // the top end as `top`, every other `middle`. A date `NaN` has no
            // position: Vega draws it at the group's origin.
            let baseline = if t <= 0.0 {
                "bottom"
            } else if t >= 1.0 {
                "top"
            } else {
                "middle"
            };
            let rise = if t.is_nan() { 0.0 } else { self.length * (1.0 - t) };
            let label_y = bar_y + rise + vega_fit::baseline_shift(baseline, label_size);
            let _ = write!(
                svg,
                "<text x=\"{label_x:.2}\" y=\"{label_y:.2}\" text-anchor=\"start\" font-family=\"{}\" \
                 font-size=\"{label_size}\" font-weight=\"{}\" fill=\"{}\">{}</text>",
                vega_escape_attr(label_family),
                xml_escape_attr(&label_weight),
                xml_escape_attr(label_color),
                xml_escape(text),
            );
        }
        crate::contract::mark_group_close(svg);
        Ok(())
    }
}

/// Vega's default legend `offset` from the anchoring edge (a right legend's
/// `ceil(x2) + offset`, a bottom's `ceil(y2) + offset`, a top's own
/// `offset` above the plot below) — `vega-view-transforms/src/layout/
/// legend.js`'s `legendParams`, `case Top`: `y: Math.floor(yb.y1) - offset`.
/// Pinned against the compiled `bar_grouped` scenegraph (legend at
/// y −31 = −(18 + 13), the 13 being `legend_grid`'s one-row box height).
pub const VEGA_LEGEND_OFFSET: f64 = 18.0;
/// The fixed gap Vega's `legend.layout` inserts between two stacked right
/// legends (a side edge's, or a top edge's along it) — probe-confirmed constant across entry counts and label/title
/// font sizes.
pub const VEGA_LEGEND_GROUP_MARGIN: f64 = 8.0;

/// Legend boxes `(w, h)` in registration order, stacked `VEGA_LEGEND_GROUP_MARGIN`
/// apart along x (`along_x`) or y: each box's offset along the stack, and the
/// stack's `(width, height)`.
pub(crate) fn stack_legends(blocks: &[(f64, f64)], along_x: bool) -> (Vec<f64>, (f64, f64)) {
    let mut cursor = 0.0_f64;
    let (mut across, mut offsets) = (0.0_f64, Vec::new());
    for &(w, h) in blocks {
        offsets.push(cursor);
        cursor += if along_x { w } else { h } + VEGA_LEGEND_GROUP_MARGIN;
        across = across.max(if along_x { h } else { w });
    }
    let along = (cursor - VEGA_LEGEND_GROUP_MARGIN).max(0.0);
    (offsets, if along_x { (along, across) } else { (across, along) })
}

/// A legend's start-anchored origin (its group's top-left, in plot
/// coordinates) per `legendParams` (vega-view-transforms `layout/legend.js`):
/// a floating one takes its corner (`legend_overlay_origin`, `plot` being the
/// plot rect's size); a reserved strip's side legend reads `x_bounds` (x1,
/// x2), a top or bottom one
/// `y_bounds` (y1, y2). Those are Vega's `xBounds`/`yBounds`: the plot rect,
/// the axes of that orientation (a y axis joins `xBounds`, an x axis
/// `yBounds`) and every scope or frame mark's bounds; a bare mark joins only
/// the view bounds (`ViewLayout.js:62-84`). The far edges floor and are
/// placed by the legend's own far side (`column`/`row: End`); the near edges
/// ceil.
pub fn legend_origin(
    position: &LegendPosition,
    x_bounds: (f64, f64),
    y_bounds: (f64, f64),
    plot: (f64, f64),
    size: (f64, f64),
) -> (f64, f64) {
    if position.overlay {
        return legend_overlay_origin(position, plot, size);
    }
    let (box_w, box_h) = size;
    match position.edge.as_str() {
        "left" => (x_bounds.0.floor() - VEGA_LEGEND_OFFSET - box_w, 0.0),
        "right" => (x_bounds.1.ceil() + VEGA_LEGEND_OFFSET, 0.0),
        "top" => (0.0, y_bounds.0.floor() - VEGA_LEGEND_OFFSET - box_h),
        "bottom" => (0.0, y_bounds.1.ceil() + VEGA_LEGEND_OFFSET),
        other => unreachable!("legend edge {other:?}: `legend_position` refuses a non-cardinal edge at deserialization"),
    }
}

/// Where a floating legend's group sits, in plot coordinates: Vega's corner
/// orient for the placement (`legend_orient`: `{edge}-right` for an
/// end-aligned top or bottom legend, `{edge}-left` otherwise, `top-{edge}`
/// for a side), inset `VEGA_LEGEND_OFFSET` from the plot's two edges
/// (`legendParams`' corner branches), the far-edge coordinate rounded.
/// Probed on production's own specs: a 222×30 box on a 449×300.67 plot sits
/// at (18, 18), (209, 18), (18, 253) and (209, 253); the plot keeps its size,
/// since a corner legend joins no view bound (`ViewLayout.js`).
pub fn legend_overlay_origin(position: &LegendPosition, plot: (f64, f64), size: (f64, f64)) -> (f64, f64) {
    let (pw, ph) = plot;
    let (box_w, box_h) = size;
    let near = VEGA_LEGEND_OFFSET;
    let far_x = d3rs::js_math::js_round(pw - VEGA_LEGEND_OFFSET - box_w);
    let far_y = d3rs::js_math::js_round(ph - VEGA_LEGEND_OFFSET - box_h);
    match (position.edge.as_str(), position.align) {
        ("top", LegendAlign::End) => (far_x, near),
        ("top", _) | ("left", _) => (near, near),
        ("bottom", LegendAlign::End) => (far_x, far_y),
        ("bottom", _) => (near, far_y),
        ("right", _) => (far_x, near),
        (other, _) => unreachable!("legend edge {other:?}: `legend_position` refuses a non-cardinal edge at deserialization"),
    }
}

/// The ink a reserved legend at `origin` (its group's top-left, in plot
/// coordinates) with `size` (box w, h) adds to the autosize-fit view bounds:
/// only its own axis, one point per edge — `ViewLayout.js:127-129` unions a
/// left legend as `add(x1, 0)`, a right one `add(x2, 0)`, a top one
/// `add(0, y1)` and a bottom one `add(0, y2)`. The orthogonal extent never
/// counts, so a legend taller (or wider) than its plot still gives the plot
/// nothing to shrink by. A floating legend adds nothing: only the four
/// cardinal orients join the view bounds.
pub fn legend_fit_bounds(position: &LegendPosition, origin: (f64, f64), size: (f64, f64)) -> Option<vega_fit::Bounds> {
    if position.overlay {
        return None;
    }
    let (x, y) = origin;
    let (box_w, box_h) = size;
    let (px, py) = match position.edge.as_str() {
        "left" => (x, 0.0),
        "right" => (x + box_w, 0.0),
        "top" => (0.0, y),
        "bottom" => (0.0, y + box_h),
        other => unreachable!("legend edge {other:?}: `legend_position` refuses a non-cardinal edge at deserialization"),
    };
    Some(vega_fit::Bounds::new(px, py, px, py))
}

/// A Vega legend group: the entry grid under a title band of
/// `titleFontSize + titlePadding` (text bounds are one font size tall;
/// none when the title is suppressed), the box `ceil(max(title, entries))`
/// wide. Probe-pinned at orient right: entries at y 17 under a 12px title
/// and 19 under 14px; a 56-wide box from 55.45px entries, 161 from a
/// wider title.
pub struct VegaLegend {
    pub grid: LegendGrid,
    /// The entry drawn in each grid slot, slots row-major: legend order,
    /// except a vertical legend over several columns (`entry_columns`).
    pub order: Vec<usize>,
    /// The drawn title; empty when none is reserved.
    pub title: String,
    pub title_band: f64,
    pub box_w: f64,
}

impl VegaLegend {
    pub fn box_h(&self) -> f64 {
        self.title_band + self.grid.box_h
    }
}

/// Vega's own legend limits (vega-parser's default `config.legend`), left in
/// force when the theme names none: the widest label and title in px.
pub const VEGA_LEGEND_LABEL_LIMIT: f64 = 160.0;
pub const VEGA_LEGEND_TITLE_LIMIT: f64 = 180.0;

/// A legend's texts as Vega draws them under `labelLimit`/`titleLimit`: a wider
/// text is drawn truncated with an ellipsis but bounded at the limit,
/// `min(measured full text, limit)` (`docs/laws-shared.md#title-ink-cap-minmeasured-limit`),
/// so the box follows the limit, not the shorter drawn text. Shaped, because
/// Vega measures a legend's own text.
pub struct LimitedLegendText {
    pub labels: Vec<String>,
    pub label_ws: Vec<f64>,
    pub title: String,
    pub title_w: f64,
}

/// An empty `title` stays empty at width 0 (no title band is reserved).
pub fn limit_legend_text<'a>(
    labels: impl IntoIterator<Item = &'a str>,
    title: &str,
    cfg: &LegendConfig,
    fonts: &FontBook,
) -> Result<LimitedLegendText, String> {
    let (size, family) = (cfg.label.font.size_or(11.0), cfg.label.font.family_or("sans-serif"));
    let limit = cfg.label.max_width.unwrap_or(VEGA_LEGEND_LABEL_LIMIT);
    let (mut drawn, mut label_ws) = (Vec::new(), Vec::new());
    for label in labels {
        label_ws.push(fonts.measure_shaped(label, family, size)?.min(limit));
        drawn.push(truncate_label(label, limit, family, size, fonts, true)?);
    }
    if title.is_empty() {
        return Ok(LimitedLegendText { labels: drawn, label_ws, title: String::new(), title_w: 0.0 });
    }
    let (title_family, title_size) = (cfg.title.font.family_or("sans-serif"), cfg.title.font.size_or(12.0));
    let title_w = fonts.measure_shaped(title, title_family, title_size)?.min(VEGA_LEGEND_TITLE_LIMIT);
    let title = truncate_label(title, VEGA_LEGEND_TITLE_LIMIT, title_family, title_size, fonts, true)?;
    Ok(LimitedLegendText { labels: drawn, label_ws, title, title_w })
}

/// Lay `entries` out as Vega would under `title_text` (empty for none).
pub fn vega_legend(
    entries: &[LegendEntry],
    title_text: &str,
    cfg: &LegendConfig,
    fonts: &FontBook,
) -> Result<VegaLegend, String> {
    let size = cfg.label.font.size_or(11.0);
    let family = cfg.label.font.family_or("sans-serif");
    // Vega sizes the legend from its own text metrics, so the widths are the
    // shaped ones (`docs/laws-shared.md#which-measurer-a-width-takes-shaped-vs-raw`).
    let ws = entries
        .iter()
        .map(|e| fonts.measure_shaped(e.label, family, size))
        .collect::<Result<Vec<_>, _>>()?;
    let title_w = if title_text.is_empty() {
        0.0
    } else {
        fonts.measure_shaped(title_text, cfg.title.font.family_or("sans-serif"), cfg.title.font.size_or(12.0))?
    };
    let symbols: Vec<Option<LegendSymbol>> = entries.iter().map(|e| e.symbol).collect();
    legend_from_widths(&ws, &symbols, title_text, title_w, cfg)
}

/// Vega's `entryColumns` (vega-parser `guides/guide-util.js:23-26`): the
/// authored `columns`, else one column for a vertical legend and every entry
/// in one row (0) for a horizontal one. Production forwards only a positive
/// count (`legend_to_vl`, `vl_field_maps.py:1050-1051`).
pub fn entry_columns(cfg: &LegendConfig) -> usize {
    match cfg.columns {
        Some(columns) if columns > 0 => columns as usize,
        _ => usize::from(cfg.direction == "vertical"),
    }
}

/// [`vega_legend`]'s layout once every text width is known: `label_ws` (one
/// per entry) and `title_w`. A caller whose texts Vega truncates at
/// `labelLimit`/`titleLimit` passes the width Vega bounds them at,
/// `min(measured full text, limit)` (`docs/laws-shared.md#title-ink-cap-minmeasured-limit`),
/// with the truncated text as `title_text`/the drawn labels.
pub fn legend_from_widths(
    label_ws: &[f64],
    symbols: &[Option<LegendSymbol>],
    title_text: &str,
    title_w: f64,
    cfg: &LegendConfig,
) -> Result<VegaLegend, String> {
    let size = cfg.label.font.size_or(11.0);
    let label_offset = cfg.label.padding.ok_or("legend.label.padding absent (theme required)")?;
    let columns = entry_columns(cfg);
    // A vertical legend over several columns fills column-major: vega-parser
    // sorts its entries by `[index % ceil(n / columns), index]`
    // (`legend-symbol-groups.js:140-144`) and `gridLayout` places the sorted
    // entries row-major; a horizontal one keeps legend order (`:146-151`).
    let mut order: Vec<usize> = (0..label_ws.len()).collect();
    if columns > 1 && cfg.direction == "vertical" {
        let rows = label_ws.len().div_ceil(columns);
        order.sort_by_key(|&idx| (idx % rows, idx));
    }
    let slot_ws: Vec<f64> = order.iter().map(|&idx| label_ws[idx]).collect();
    let slot_symbols: Vec<Option<LegendSymbol>> = order.iter().map(|&idx| symbols[idx]).collect();
    let grid = legend_grid(&slot_ws, &slot_symbols, size, columns, label_offset);
    let title_size = cfg.title.font.size_or(12.0);
    let title_band = if title_text.is_empty() {
        0.0
    } else if label_ws.is_empty() {
        // No entries: the box is the title's own ink, clipped at the origin.
        vega_fit::text_ink(0.0, 0.0, title_w, title_size, "left", "top").y2
    } else {
        title_size + VEGA_LEGEND_TITLE_PADDING
    };
    let box_w = grid.width.max(title_w).ceil();
    Ok(VegaLegend { grid, order, title: title_text.to_string(), title_band, box_w })
}

/// The symbol Vega draws per legend entry: a size-100 square stroked in
/// the mark border color (bar), a size-100 circle stroked in its own fill
/// (line/area), or a fixed 20px horizontal segment stroked in its own fill
/// at `width` (a layered chart's mixed legend gives a `line` layer's entry
/// this "stroke" glyph — `_mixed_mark_legend_symbols`, `_overlay.py:649` —
/// distinct from a plain multi-series line chart's own circle default). A
/// filled point mark (scatter) carries no stroke color, so its symbol paints
/// the fill alone and bounds the bare 10px glyph.
#[derive(Clone, Copy)]
pub enum LegendSymbol<'a> {
    Square(&'a str),
    Circle,
    /// A circle stroked in the mark's own constant `stroke` color: a filled
    /// `arc` mark's legend symbol takes the mark's `stroke` and keeps
    /// Vega's `symbolStrokeWidth` 1.5 (`symbols` in Vega-Lite's
    /// `legend/encode.ts` forwards the mark `stroke` value, not its width).
    StrokedCircle(&'a str),
    /// A default circle ringed in a constant `stroke`, as a bar's own squares
    /// are: a point layer's entries beside bars. Unlike [`LegendSymbol::StrokedCircle`]
    /// it keeps the legend's uniform cell, as a square and circle always do.
    BorderedCircle(&'a str),
    FilledCircle,
    /// Vega's default 10px stroke symbol (`M-5,0L5,0`), the legend of a line
    /// mark outside a mixed stroke legend.
    ShortStroke,
    /// A default circle drawn at the given opacity: an `area` layer's entry.
    FadedCircle(f64),
    /// A fill-only 10px square: a filled `rect` mark's legend symbol carries
    /// no stroke color (Vega-Lite `symbolType` is `square` for `rect`,
    /// `src/compile/legend/properties.ts:95-116`; a fill legend keeps only the
    /// fill, `src/compile/legend/encode.ts:65-67`), so it bounds the bare glyph.
    FilledSquare,
    /// A fill-only shape-scale glyph, path `d` at size 100: a color legend
    /// a shape channel on the same field merges into (Vega's own symbol
    /// paths, `scatter_shape::path`). Every Vega symbol at size 100 fits the
    /// r-5 circle's box, so it lays out as [`LegendSymbol::FilledCircle`].
    FilledGlyph(&'a str),
    /// An unfilled point mark's legend glyph, path `d`: the mark's own interior
    /// fill under a stroke in the entry's color, at the mark's opacity, the
    /// glyph's half extents given for the legend grid.
    Ring(&'a str, &'a str, f64, (f64, f64)),
    Stroke(f64),
    /// A strokeDash legend's symbol: a size-2000 horizontal segment
    /// (`M-22.361,0L22.361,0`) stroked in the entry's own color at 1.5 with
    /// the series' dash pattern (`""` for a solid one), round-capped.
    Dash(&'a str),
    /// A strokeDash legend drawn apart from the color legend: a size-100 circle with
    /// no fill, stroked at 1.5 in the entry's own color (the halo's canvas) with the
    /// series' dash pattern (`""` for a solid one).
    DashCircle(&'a str),
    /// A filled glyph whose bare half extents (x, y) are given — a shape
    /// channel's triangles are shorter than the circle along one axis. Laid
    /// out by [`legend_grid`] only; nothing draws through
    /// [`emit_vega_legend`].
    Glyph(f64, f64),
}

/// A legend dash sample's ` stroke-dasharray` attribute; empty for a solid one.
fn dasharray_attr(pattern: &str) -> String {
    if pattern.is_empty() {
        String::new()
    } else {
        format!(" stroke-dasharray=\"{}\"", xml_escape_attr(pattern))
    }
}

/// The legend Vega draws for a scale no theme styles: `themed`'s fonts at Vega's own
/// defaults (`labelFontSize` 10, `titleFontSize` 11 bold, `labelOffset` 4, black text) in
/// a vertical column at the right.
pub fn vega_default_legend(themed: &LegendConfig) -> LegendConfig {
    let font = |source: &Font, size: f64, weight: &str| Font {
        family: source.family.clone(),
        color: Some("#000".into()),
        size: Some(size),
        weight: Some(serde_json::Value::from(weight)),
        ..Font::default()
    };
    LegendConfig {
        visible: true,
        position: LegendPosition { edge: "right".into(), align: LegendAlign::Start, overlay: false },
        direction: "vertical".into(),
        columns: None,
        label: LegendLabel { font: font(&themed.label.font, 10.0, "400"), padding: Some(4.0), max_width: None },
        title: LegendTitle { font: font(&themed.title.font, 11.0, "bold"), visible: None },
        values: None,
        symbol_limit: None,
        symbol_shape: None,
        symbol_fill: None,
    }
}

/// Draw a legend the way Vega lays out a symbol legend (`VegaLegend`): the
/// title (when the band is reserved) at baseline top, then each entry's
/// 10px symbol (stroke 1.5) centered at `size/2` in its cell, the label
/// `size + labelOffset` in at middle baseline, entries at the grid's
/// column x / row y under the title band. An entry carrying its own
/// `entry.symbol` paints that instead of the shared `default_symbol` — a
/// layered chart's mixed legend (`_mixed_mark_legend_symbols`,
/// `_overlay.py:649`), one entry per paint layer with that layer's own
/// mark's glyph.
#[allow(clippy::too_many_arguments)]
pub fn emit_vega_legend(
    out: &mut String,
    entries: &[LegendEntry],
    legend: &VegaLegend,
    cfg: &LegendConfig,
    default_symbol: LegendSymbol,
    legend_x: f64,
    legend_y: f64,
    stamp_series: bool,
) {
    crate::contract::legend_open(out);
    if !legend.title.is_empty() {
        crate::contract::legend_title_open(out);
        let tf = &cfg.title.font;
        let tfs = tf.size_or(12.0);
        let _ = write!(
            out,
            "<text x=\"{legend_x:.2}\" y=\"{:.2}\" text-anchor=\"start\" \
             font-family=\"{}\" font-size=\"{tfs}\" font-weight=\"{}\" fill=\"{}\">{}</text>",
            legend_y + vega_fit::baseline_shift("top", tfs),
            vega_escape_attr(tf.family_or("sans-serif")),
            xml_escape_attr(&tf.weight_str()),
            xml_escape_attr(tf.color.as_deref().unwrap_or("#313233")),
            xml_escape(&legend.title)
        );
        crate::contract::mark_group_close(out);
    }
    let legend_y = legend_y + legend.title_band;
    let grid = &legend.grid;
    let label_font = &cfg.label.font;
    let size = label_font.size_or(11.0);
    let family = label_font.family.as_deref().unwrap_or("sans-serif");
    let color = label_font.color.as_deref().unwrap_or("#626366");
    let weight = label_font.weight_str();
    let columns = grid.col_x.len();
    crate::contract::legend_entries_open(out);
    for (i, entry) in legend.order.iter().map(|&idx| &entries[idx]).enumerate() {
        let x = legend_x + grid.col_x[i % columns];
        let y = legend_y + grid.row_y[i / columns];
        let c = grid.size / 2.0;
        let fill = entry.fill;
        crate::contract::legend_symbol_open(out);
        match entry.symbol.unwrap_or(default_symbol) {
            LegendSymbol::Square(stroke) => {
                let _ = write!(
                    out,
                    "<rect x=\"{:.2}\" y=\"{:.2}\" width=\"10\" height=\"10\" \
                     fill=\"{fill}\" stroke=\"{stroke}\" stroke-width=\"1.5\"/>",
                    x + c - 5.0,
                    y + c - 5.0, fill = xml_escape_attr(fill), stroke = xml_escape_attr(stroke)
                );
            }
            LegendSymbol::Circle => {
                let _ = write!(
                    out,
                    "<path transform=\"translate({:.2},{:.2})\" \
                     d=\"M5,0A5,5,0,1,1,-5,0A5,5,0,1,1,5,0\" \
                     fill=\"{fill}\" stroke=\"{fill}\" stroke-width=\"1.5\"/>",
                    x + c,
                    y + c, fill = xml_escape_attr(fill)
                );
            }
            LegendSymbol::ShortStroke => {
                let _ = write!(
                    out,
                    "<path transform=\"translate({:.2},{:.2})\" d=\"M-5,0L5,0\" \
                     fill=\"{fill}\" stroke=\"{fill}\" stroke-width=\"1.5\"/>",
                    x + c,
                    y + c, fill = xml_escape_attr(fill)
                );
            }
            LegendSymbol::FadedCircle(opacity) => {
                let _ = write!(
                    out,
                    "<path transform=\"translate({:.2},{:.2})\" \
                     d=\"M5,0A5,5,0,1,1,-5,0A5,5,0,1,1,5,0\" \
                     fill=\"{fill}\" stroke=\"{fill}\" stroke-width=\"1.5\" opacity=\"{opacity}\"/>",
                    x + c,
                    y + c, fill = xml_escape_attr(fill)
                );
            }
            LegendSymbol::StrokedCircle(stroke) | LegendSymbol::BorderedCircle(stroke) => {
                let _ = write!(
                    out,
                    "<path transform=\"translate({:.2},{:.2})\" \
                     d=\"M5,0A5,5,0,1,1,-5,0A5,5,0,1,1,5,0\" \
                     fill=\"{fill}\" stroke=\"{stroke}\" stroke-width=\"1.5\"/>",
                    x + c,
                    y + c, fill = xml_escape_attr(fill), stroke = xml_escape_attr(stroke)
                );
            }
            LegendSymbol::FilledCircle => {
                let _ = write!(
                    out,
                    "<path transform=\"translate({:.2},{:.2})\" \
                     d=\"M5,0A5,5,0,1,1,-5,0A5,5,0,1,1,5,0\" \
                     fill=\"{fill}\" stroke-width=\"1.5\"/>",
                    x + c,
                    y + c, fill = xml_escape_attr(fill)
                );
            }
            LegendSymbol::FilledGlyph(d) => {
                let _ = write!(
                    out,
                    "<path transform=\"translate({:.2},{:.2})\" d=\"{d}\" fill=\"{fill}\" stroke-width=\"1.5\"/>",
                    x + c,
                    y + c,
                    fill = xml_escape_attr(fill)
                );
            }
            LegendSymbol::Ring(d, interior, opacity, _) => {
                let _ = write!(
                    out,
                    "<path transform=\"translate({:.2},{:.2})\" d=\"{d}\" fill=\"{}\" stroke=\"{}\" \
                     stroke-width=\"1.5\" opacity=\"{opacity}\"/>",
                    x + c,
                    y + c, xml_escape_attr(interior), xml_escape_attr(fill)
                );
            }
            LegendSymbol::FilledSquare => {
                let _ = write!(
                    out,
                    "<rect x=\"{:.2}\" y=\"{:.2}\" width=\"10\" height=\"10\" \
                     fill=\"{fill}\" stroke-width=\"1.5\"/>",
                    x + c - 5.0,
                    y + c - 5.0, fill = xml_escape_attr(fill)
                );
            }
            LegendSymbol::Dash(pattern) => {
                let dash = dasharray_attr(pattern);
                let half = VEGA_DASH_SYMBOL_SIZE.sqrt() / 2.0;
                let _ = write!(
                    out,
                    "<path transform=\"translate({:.2},{:.2})\" d=\"M-{half:.3},0L{half:.3},0\" \
                     fill=\"{fill}\" stroke=\"{fill}\" stroke-width=\"1.5\" stroke-linecap=\"round\"{dash}/>",
                    x + c,
                    y + c, fill = xml_escape_attr(fill)
                );
            }
            LegendSymbol::DashCircle(pattern) => {
                let dash = dasharray_attr(pattern);
                let _ = write!(
                    out,
                    "<path transform=\"translate({:.2},{:.2})\" d=\"M5,0A5,5,0,1,1,-5,0A5,5,0,1,1,5,0\" \
                     fill=\"none\" stroke=\"{fill}\" stroke-width=\"1.5\" stroke-linecap=\"round\"{dash}/>",
                    x + c,
                    y + c, fill = xml_escape_attr(fill)
                );
            }
            LegendSymbol::Glyph(..) => unreachable!("a glyph symbol is laid out, never emitted here"),
            LegendSymbol::Stroke(width) => {
                let _ = write!(
                    out,
                    "<path transform=\"translate({:.2},{:.2})\" d=\"M-10,0L10,0\" \
                     fill=\"{fill}\" stroke=\"{fill}\" stroke-width=\"{width}\"/>",
                    x + c,
                    y + c, fill = xml_escape_attr(fill)
                );
            }
        }
        crate::contract::mark_group_close(out);
        // `.role-legend-label` -- `chart_interactivity.js`'s `legendSeriesOrder`
        // reads this class + join key off Vega's own legend-label group
        // (`chart_svg_dev/contract.py::extract_contract`'s `legend_labels`
        // key). `data-dbt-series` -- the untruncated join key
        // `_stamp_series_keys` (converters/chart.py:367-385) stamps -- fires
        // exactly when `stamp_series` is true: a series row was promoted
        // with NO `ROLE_ORDER` rank baked alongside it (the caller computes
        // this once from the SAME `order_domain`/series-promotion facts its
        // own tooltip already derived, never a second scan of the SVG).
        let series_attr = entry.series_attr(stamp_series);
        crate::contract::legend_label_open(out, &series_attr);
        let _ = write!(
            out,
            "<text x=\"{:.2}\" y=\"{:.2}\" text-anchor=\"start\" \
             font-family=\"{}\" font-size=\"{size}\" font-weight=\"{weight}\" \
             fill=\"{color}\">{}</text>",
            x + grid.label_x,
            y + c + vega_fit::baseline_shift("middle", size),
            vega_escape_attr(family),
            xml_escape(entry.label), weight = xml_escape_attr(&weight), color = xml_escape_attr(color)
        );
        out.push_str("</g>");
    }
    crate::contract::mark_group_close(out);
    crate::contract::mark_group_close(out);
}

#[derive(Deserialize)]
pub struct LegendTitle {
    pub font: Font,
    #[serde(default)]
    pub visible: Option<bool>,
}

#[derive(Clone, Copy)]
pub struct LegendEntry<'a> {
    pub label: &'a str,
    pub fill: &'a str,
    /// Overrides `emit_vega_legend`'s shared symbol for this one entry —
    /// `None` for every existing single-mark caller. A layered chart's base
    /// entry sets its own family's symbol explicitly too (rather than
    /// relying on the shared default), so the legend reads right the
    /// moment a second, differently-marked entry joins it.
    pub symbol: Option<LegendSymbol<'a>>,
    /// The untruncated series name `data-dbt-series` joins on when `label` is
    /// the ellipsized form Vega draws past `labelLimit`; `None` when `label`
    /// is the whole name.
    pub series_key: Option<&'a str>,
}

impl LegendEntry<'_> {
    /// The ` data-dbt-series` attribute (`_stamp_series_keys`,
    /// `converters/chart.py:457-461`: the label's text, stripped), or nothing
    /// when `stamp_series` is off.
    fn series_attr(&self, stamp_series: bool) -> String {
        if stamp_series {
            let key = self.series_key.unwrap_or(self.label).trim();
            format!(" data-dbt-series=\"{}\"", xml_escape_attr(key))
        } else {
            String::new()
        }
    }
}

/// A left or bottom legend anchors on the bounds `legend_origin` reads,
/// which a family models on its fit arm alone: beside `blocker` (an arm or an
/// overlay whose bounds that union leaves out) it refuses by name.
pub fn refuse_far_edge_legend(
    family: &str,
    shown: bool,
    cfg: &LegendConfig,
    blocker: Option<&str>,
) -> Result<(), String> {
    let edge = cfg.position.edge.as_str();
    match blocker {
        Some(blocker) if shown && matches!(edge, "left" | "bottom") => {
            Err(format!("{family}: legend.position={edge:?} beside {blocker} is not modeled"))
        }
        _ => Ok(()),
    }
}

/// A legacy margin arm draws its measured one-column legend
/// (`emit_vertical_legend`), so a vertical legend over several `entry_columns`
/// refuses there by name.
pub fn refuse_legacy_columns(family: &str, cfg: &LegendConfig) -> Result<(), String> {
    let columns = entry_columns(cfg);
    if cfg.direction == "vertical" && columns > 1 {
        return Err(format!(
            "{family}: legend.columns={columns} on a vertical legend is not modeled on the legacy margin arm \
             — its measured legend is one column"
        ));
    }
    Ok(())
}

/// Pixel width the vertical legend panel claims: swatch + gap + the wider of
/// the widest entry label or the title text.
pub fn vertical_legend_width(
    entries: &[LegendEntry],
    title_text: &str,
    cfg: &LegendConfig,
    fonts: &FontBook,
) -> Result<f64, String> {
    let leg_label_font = &cfg.label.font;
    let leg_label_size = leg_label_font.size_or(11.0);
    let leg_label_family = leg_label_font.family_or("sans-serif");
    let leg_title_font = &cfg.title.font;
    let leg_title_size = leg_title_font.size_or(12.0);
    let leg_title_family = leg_title_font.family_or("sans-serif");

    let max_label_w = entries.iter().try_fold(0.0_f64, |acc, e| {
        fonts.measure(e.label, leg_label_family, leg_label_size).map(|w| acc.max(w))
    })?;
    let title_w = if title_text.is_empty() {
        0.0
    } else {
        fonts.measure(title_text, leg_title_family, leg_title_size)?
    };
    Ok((LEGEND_SYMBOL_BOX + LEGEND_LABEL_GAP + max_label_w.max(title_w)).max(52.0))
}

/// Pixel height the vertical legend panel claims: title band (when the
/// title actually renders — an empty `title_text`, including `title.visible:
/// false` folded into it by `legend_title_text`, claims none) plus one row
/// per entry.
pub fn vertical_legend_height(entries: &[LegendEntry], title_text: &str) -> f64 {
    let title_band = if title_text.is_empty() { 0.0 } else { LEGEND_TITLE_H };
    title_band + entries.len() as f64 * LEGEND_ROW_H
}

/// Title text for a legend: the color field's display name, cased per the
/// legend's own title font (production's `field_display_text`) — empty when
/// `legend.title.visible: false` is authored, production's own signal to
/// drop the title from layout entirely, not just hide the text. The wide
/// fold's synthetic color field names no title (`fold_wide_measures`'s
/// `"title": None`).
pub fn legend_title_text(
    color_field: &str,
    cfg: &LegendConfig,
    field_labels: &FieldLabels,
) -> Result<String, String> {
    if cfg.title.visible == Some(false) || color_field == crate::common::WIDE_LABEL_FIELD {
        return Ok(String::new());
    }
    color_channel_title(color_field, cfg, field_labels)
}

/// The color channel's title in Vega-Lite's own mark description: the field's
/// display name, cased per the legend title's font, whether or not the legend
/// draws it.
pub fn color_channel_title(
    color_field: &str,
    cfg: &LegendConfig,
    field_labels: &FieldLabels,
) -> Result<String, String> {
    field_display_text(color_field, field_labels, cfg.title.font.case.as_deref().unwrap_or("title"))
}

/// Emit the vertical legend (title + swatch/label rows), top-left corner at `(x, y)`.
pub fn emit_vertical_legend(
    svg: &mut String,
    entries: &[LegendEntry],
    title_text: &str,
    cfg: &LegendConfig,
    x: f64,
    y: f64,
    stamp_series: bool,
) {
    let leg_label_font = &cfg.label.font;
    let leg_label_size = leg_label_font.size_or(11.0);
    let leg_label_family = leg_label_font.family_or("sans-serif");
    let leg_label_color = leg_label_font.color.as_deref().unwrap_or("#626366");
    let leg_label_weight = leg_label_font.weight_str();

    let leg_title_font = &cfg.title.font;
    let leg_title_size = leg_title_font.size_or(12.0);
    let leg_title_family = leg_title_font.family_or("sans-serif");
    let leg_title_color = leg_title_font.color.as_deref().unwrap_or("#313233");
    let leg_title_weight = leg_title_font.weight_str();

    crate::contract::legend_open(svg);
    if !title_text.is_empty() {
        crate::contract::legend_title_open(svg);
        let _ = write!(
            svg,
            "<text x=\"{x:.2}\" y=\"{:.2}\" text-anchor=\"start\" \
             font-family=\"{}\" font-size=\"{leg_title_size}\" \
             font-weight=\"{leg_title_weight}\" fill=\"{leg_title_color}\">{}</text>",
            y + leg_title_size * 0.75,
            vega_escape_attr(leg_title_family),
            xml_escape(title_text), leg_title_weight = xml_escape_attr(&leg_title_weight), leg_title_color = xml_escape_attr(leg_title_color)
        );
        crate::contract::mark_group_close(svg);
    }

    let title_band = if title_text.is_empty() { 0.0 } else { LEGEND_TITLE_H };
    crate::contract::legend_entries_open(svg);
    for (i, entry) in entries.iter().enumerate() {
        let item_y = y + title_band + i as f64 * LEGEND_ROW_H;
        let cx = x + LEGEND_SYMBOL_BOX / 2.0;
        let cy = item_y + LEGEND_SYMBOL_BOX / 2.0;
        crate::contract::legend_symbol_open(svg);
        let _ = write!(
            svg,
            "<path transform=\"translate({cx:.2},{cy:.2})\" \
             d=\"M{LEGEND_SYMBOL_R},0A{LEGEND_SYMBOL_R},{LEGEND_SYMBOL_R},0,1,1,\
             -{LEGEND_SYMBOL_R},0A{LEGEND_SYMBOL_R},{LEGEND_SYMBOL_R},0,1,1,{LEGEND_SYMBOL_R},0\" \
             fill=\"{}\"/>",
            xml_escape_attr(entry.fill)
        );
        crate::contract::mark_group_close(svg);
        let tx = x + LEGEND_SYMBOL_BOX + LEGEND_LABEL_GAP;
        let ty = item_y + LEGEND_SYMBOL_BOX / 2.0 + leg_label_size * 0.27;
        // See `emit_vega_legend`'s doc comment on the `.role-legend-label`
        // wrap and the `data-dbt-series` stamp gate.
        let series_attr = entry.series_attr(stamp_series);
        crate::contract::legend_label_open(svg, &series_attr);
        let _ = write!(
            svg,
            "<text x=\"{tx:.2}\" y=\"{ty:.2}\" text-anchor=\"start\" \
             font-family=\"{}\" font-size=\"{leg_label_size}\" \
             font-weight=\"{leg_label_weight}\" fill=\"{leg_label_color}\">{}</text>",
            vega_escape_attr(leg_label_family),
            xml_escape(entry.label), leg_label_weight = xml_escape_attr(&leg_label_weight), leg_label_color = xml_escape_attr(leg_label_color)
        );
        svg.push_str("</g>");
    }
    crate::contract::mark_group_close(svg);
    crate::contract::mark_group_close(svg);
}

#[cfg(test)]
mod tests {
    #[test]
    fn legends_stack_a_margin_apart_along_their_axis() {
        let blocks = [(30.0, 10.0), (20.0, 40.0), (50.0, 5.0)];
        let (down, size) = stack_legends(&blocks, false);
        assert_eq!((down, size), (vec![0.0, 18.0, 66.0], (50.0, 71.0)));
        let (along, size) = stack_legends(&blocks, true);
        assert_eq!((along, size), (vec![0.0, 38.0, 66.0], (116.0, 40.0)));
        assert_eq!(stack_legends(&[], false), (vec![], (0.0, 0.0)));
    }

    #[test]
    fn a_reserved_strip_reads_as_its_edge_and_align() {
        let position = serde_json::json!({"edge": "right", "align": "start", "overlay": false});
        let read = legend_position(position).unwrap();
        assert_eq!((read.edge.as_str(), read.align), ("right", LegendAlign::Start));
        let centered = serde_json::json!({"edge": "top", "align": "center", "overlay": false});
        assert_eq!(legend_position(centered).unwrap().align, LegendAlign::Center);
    }

    /// `resolve_legend_entries`' two tiers: the exact spelling, else the one
    /// folded match; an unmatched or ambiguous token drops; nothing resolving
    /// leaves the domain's own order.
    #[test]
    fn authored_values_resolve_exact_then_folded_and_drop_what_does_not_match() {
        let domain = ["Net Revenue", "Net-Revenue 2", "Tools"];
        let authored = |tokens: &[&str]| tokens.iter().map(|t| t.to_string()).collect::<Vec<_>>();
        let resolve = |tokens: &[&str]| resolve_legend_values(&authored(tokens), &domain, None);
        assert_eq!(resolve(&["Tools", "net_revenue"]), [2, 0]);
        assert_eq!(resolve(&["tools", "Tools", "nothing"]), [2]);
        // "net revenue 2" folds to exactly one entry; "net-revenue" to the
        // first only, so each resolves.
        assert_eq!(resolve_legend_values(&authored(&["net revenue 2"]), &domain, None), [1]);
        // Nothing resolves: the whole domain, in its own order.
        assert_eq!(resolve_legend_values(&authored(&["x", "y"]), &domain, None), [0, 1, 2]);
        // Two entries folding to one key make the token ambiguous: dropped.
        assert_eq!(resolve_legend_values(&authored(&["a b"]), &["A-B", "a_b"], None), [0, 1]);
    }

    /// A wide fold's raw measure name resolves to its humanized entry
    /// (`wide_legend_aliases`); an alias two entries share, or one that spells
    /// another entry's own name, resolves to neither.
    #[test]
    fn authored_values_resolve_an_alias_after_the_exact_spelling() {
        let authored = |tokens: &[&str]| tokens.iter().map(|t| t.to_string()).collect::<Vec<_>>();
        let domain = ["growth %", "total cost"];
        let aliases: &[&str] = &["growth_pct", "total_cost"];
        let resolve =
            |tokens: &[&str]| resolve_legend_values(&authored(tokens), &domain, Some(aliases));
        assert_eq!(resolve(&["growth_pct", "total cost"]), [0, 1]);
        assert_eq!(resolve(&["total_cost", "growth %"]), [1, 0]);
        assert_eq!(resolve(&["Total-Cost"]), [1]);
        assert_eq!(resolve(&["growth pct"]), [0]);
        let shared: &[&str] = &["x", "x"];
        assert_eq!(resolve_legend_values(&authored(&["x"]), &domain, Some(shared)), [0, 1]);
        let stolen: &[&str] = &["total cost", "total_cost"];
        assert_eq!(resolve_legend_values(&authored(&["total cost"]), &domain, Some(stolen)), [1]);
        assert_eq!(resolve_legend_values(&authored(&["total-cost"]), &domain, Some(stolen)), [1]);
    }

    /// `end` reads as a placement; whether a family may draw it is the
    /// family's own gate (`refuse_unmodeled_slide`): production's cartesian
    /// resolve raises for it, a pie key slides to the far end.
    #[test]
    fn an_end_aligned_strip_reads_and_only_a_pie_may_draw_it() {
        let end = serde_json::json!({"edge": "top", "align": "end", "overlay": false});
        assert_eq!(legend_position(end).unwrap().align, LegendAlign::End);
        let resolved = serde_json::json!({"legend": {"position": {"align": "end"}}, "multiples": null});
        assert!(refuse_unmodeled_slide("pie", &resolved).is_ok());
        for family in ["line", "area", "bar", "histogram", "heatmap", "scatter"] {
            let err = refuse_unmodeled_slide(family, &resolved).unwrap_err();
            assert!(err.starts_with("ERR-LEGEND-POSITION-UNSUPPORTED") && err.contains("\"end\""), "{err}");
        }
        // A family that draws no legend never reads the baked placement: a
        // board-wide `end` default reaches its payload, and production renders it.
        for family in ["table", "kpi", "callout", "spark_bar", "sankey", "geoshape"] {
            assert!(refuse_unmodeled_slide(family, &resolved).is_ok(), "{family}");
        }
    }

    /// A floating legend reads its corner; a side edge floats centered or
    /// not at all (`validate_cartesian_position`).
    #[test]
    fn a_floating_placement_reads_its_overlay_and_a_side_edge_floats_centered_only() {
        let floating = serde_json::json!({"edge": "top", "align": "end", "overlay": true});
        let read = legend_position(floating).unwrap();
        assert!(read.overlay && read.align == LegendAlign::End && read.edge == "top");
        let side = serde_json::json!({"edge": "right", "align": "center", "overlay": true});
        assert!(legend_position(side).unwrap().overlay);
        let err = legend_position(serde_json::json!({"edge": "left", "align": "start", "overlay": true}))
            .unwrap_err()
            .to_string();
        assert!(err.contains("floating side legend is centered"), "{err}");
    }

    /// `legendParams`' corner branches, probed on production's own specs
    /// (`legend-position-cartesian`, `legend-position-centered`): a 222×30
    /// box on a 449×300.67 plot sits 18px inside its two edges, the far edge
    /// rounded; a floating legend adds no fit bound; a centered one slides
    /// from its corner to the plot's middle and an end-aligned one never
    /// slides.
    #[test]
    fn a_floating_legend_sits_in_its_corner_adds_no_fit_bound_and_slides_from_there() {
        let at = |edge: &str, align| LegendPosition { edge: edge.into(), align, overlay: true };
        let plot = (449.0, 300.0 + 2.0 / 3.0);
        let size = (222.0, 30.0);
        assert_eq!(legend_overlay_origin(&at("top", LegendAlign::Start), plot, size), (18.0, 18.0));
        assert_eq!(legend_overlay_origin(&at("top", LegendAlign::End), plot, size), (209.0, 18.0));
        assert_eq!(legend_overlay_origin(&at("bottom", LegendAlign::Start), plot, size), (18.0, 253.0));
        assert_eq!(legend_overlay_origin(&at("bottom", LegendAlign::End), plot, size), (209.0, 253.0));
        for (ph, y) in [(259.3, 211.0), (259.7, 212.0), (260.5, 213.0)] {
            assert_eq!(legend_overlay_origin(&at("bottom", LegendAlign::Start), (449.0, ph), size).1, y);
        }
        let origin = legend_origin(&at("top", LegendAlign::End), (-40.0, 500.0), (-5.0, 400.0), plot, size);
        assert_eq!(origin, (209.0, 18.0));
        assert!(legend_fit_bounds(&at("top", LegendAlign::End), origin, size).is_none());
        assert_eq!(slide_block("c", &at("top", LegendAlign::End), size, plot).unwrap(), (0.0, 0.0));
        // `o_top`: a 176-wide box on a 454 plot centers at 139; `o_right`: a
        // 60×62 box on the 300.67 plot centers at (300.67 − 62) / 2.
        let (dx, _) = slide_block("c", &at("top", LegendAlign::Center), (176.0, 30.0), (454.0, plot.1)).unwrap();
        assert_eq!(18.0 + dx, 139.0);
        let side = at("right", LegendAlign::Center);
        let (x, y) = legend_overlay_origin(&side, (454.0, plot.1), (60.0, 62.0));
        let (_, dy) = slide_block("c", &side, (60.0, 62.0), (454.0, plot.1)).unwrap();
        assert_eq!(x, 376.0);
        assert!((y + dy - (plot.1 - 62.0) / 2.0).abs() < 1e-9);
    }

    #[test]
    fn an_unknown_edge_is_refused_by_name() {
        let sideways = serde_json::json!({"edge": "middle", "align": "start", "overlay": false});
        let err = legend_position(sideways).unwrap_err().to_string();
        assert!(err.contains("edge \"middle\" is not a legend edge"), "{err}");
    }

    #[test]
    fn a_centered_legend_is_not_refused_by_the_slide_gate() {
        let at = |align: &str, multiples: serde_json::Value| {
            serde_json::json!({"legend": {"position": {"align": align}}, "multiples": multiples})
        };
        for family in ["line", "area", "bar", "heatmap", "pie"] {
            assert!(refuse_unmodeled_slide(family, &at("center", serde_json::Value::Null)).is_ok());
        }
        let faceted = at("center", serde_json::json!({"rows": "r"}));
        assert!(refuse_unmodeled_slide("line", &faceted).is_ok(), "the ERR is raised ahead, from the payload's facts");
        assert!(refuse_unmodeled_slide("scatter", &at("start", serde_json::Value::Null)).is_ok());
        assert!(refuse_unmodeled_slide("line", &at("start", serde_json::json!({"rows": "r"}))).is_ok());
    }

    /// Production's own slides (`fixtures/boards/legend-align`): a 62px
    /// right legend in a 354px plot moves 146, a 176px top legend in a
    /// 968px plot 396 -- `(span - block) / 2`.
    #[test]
    fn legend_slide_centers_the_block_on_the_plot_span() {
        let at = |edge: &str, align| LegendPosition { edge: edge.into(), align, overlay: false };
        let plot = (968.0, 354.0);
        let block = (176.0, 62.0);
        let center = LegendAlign::Center;
        assert_eq!(slide_block("c", &at("right", center), block, plot), Ok((0.0, 146.0)));
        assert_eq!(slide_block("c", &at("top", center), block, plot), Ok((396.0, 0.0)));
        assert_eq!(slide_block("c", &at("bottom", center), block, plot), Ok((396.0, 0.0)));
        assert_eq!(slide_block("c", &at("right", LegendAlign::Start), block, plot), Ok((0.0, 0.0)));
        // `legend_align.py:100-103`: end is `span - block`.
        assert_eq!(slide_block("c", &at("right", LegendAlign::End), block, plot), Ok((0.0, 292.0)));
        assert_eq!(slide_block("c", &at("bottom", LegendAlign::End), block, plot), Ok((792.0, 0.0)));
        let err = slide_block("c", &at("right", center), (60.0, 354.0), plot).unwrap_err();
        assert!(err.contains("ERR-LEGEND-ALIGN-UNSUPPORTED") && err.contains("354px"), "{err}");
    }

    /// `legend_align.py:98` prints both extents through `:g`, six significant
    /// digits: a 120.123456px legend in a 100px plot reads `120.123px` and
    /// `100px`.
    #[test]
    fn legend_slide_refusal_prints_extents_with_format_g() {
        let position = LegendPosition { edge: "top".into(), align: LegendAlign::Center, overlay: false };
        let err = slide_block("c", &position, (120.123456, 20.0), (100.0, 50.0)).unwrap_err();
        assert_eq!(err.params["reason"], "its legend (120.123px) is larger than its plot (100px)");
    }

    /// `legend_align.py:89-93`: a plot with no span along the legend's edge
    /// refuses with production's own reason, not the larger-than one.
    #[test]
    fn legend_slide_refusal_names_an_empty_plot_span() {
        let position = LegendPosition { edge: "top".into(), align: LegendAlign::Center, overlay: false };
        let err = slide_block("c", &position, (20.0, 20.0), (0.0, 50.0)).unwrap_err();
        assert_eq!(err.params["reason"], "its plot has no size to align in");
    }

    use super::*;

    /// Probe-pinned box heights: one 11px row 13, two rows 29 (rows 0.25
    /// and 16), two 15px rows 34 (rows 0.25 and 18, where a union formula
    /// gives 33), and a one-column grid with its first row at 0.
    #[test]
    fn legend_grid_matches_vega() {
        let no_symbols = [None, None, None];
        let g = legend_grid(&[60.0, 58.0, 30.0], &no_symbols, 11.0, 0, 8.0);
        assert_eq!(g.width, 230.0);
        assert_eq!((g.size, g.label_x, g.box_h), (12.0, 20.0, 13.0));
        assert_eq!(g.row_y, vec![0.25]);
        assert_eq!(g.col_x, vec![0.0, 91.0, 180.0]);
        let g = legend_grid(&[60.0, 58.0, 30.0], &no_symbols, 11.0, 2, 8.0);
        assert_eq!((g.row_y.clone(), g.box_h), (vec![0.25, 16.0], 29.0));
        let g = legend_grid(&[60.0, 58.0, 30.0], &no_symbols, 15.0, 2, 8.0);
        assert_eq!((g.size, g.row_y.clone(), g.box_h), (15.0, vec![0.25, 18.0], 34.0));
        let g = legend_grid(&[60.0, 58.0], &no_symbols[..2], 11.0, 1, 8.0);
        assert_eq!((g.row_y.clone(), g.box_h), (vec![0.0, 16.0], 29.0));
        assert!(legend_grid(&[], &[], 11.0, 2, 8.0).row_y.is_empty());
    }

    /// `_mixed_mark_legend_symbols`'s `symbolSize`/`symbolStrokeWidth`
    /// exprs are bound to `datum.value` (`_overlay.py:679-682`) — a
    /// per-item condition Vega's own `sizeExpression` (vega-parser
    /// `legend.js:176-183`) cannot resolve outside a per-entry encode
    /// context, since it runs once as a transform PARAM with no `datum`.
    /// Probed via `vega_to_scenegraph` on the compiled
    /// `dual-y-axis-lab/revenue_vs_conversion` spec: every entry's
    /// symbol/label centers at `fontSize/2` (5.5 at 11px — both entries'
    /// own `x`/`y` are `5.5`, not `size/2` from either entry's OWN
    /// `symbolSize`/`symbolStrokeWidth`), while each entry's OWN drawn ink
    /// still follows its real shape: the square entry (size 100, stroke
    /// 1.5) reports scenegraph height 12, the stroke entry (a thin
    /// horizontal line, no radius — its ink is just half its OWN stroke
    /// width, 2) reports height 10.5. The row (and `box_h`) is the max
    /// across entries — 12, not the uniform law's 13.
    #[test]
    fn mixed_mark_legend_centers_on_font_size_not_the_uniform_cell() {
        let symbols = [Some(LegendSymbol::Square("#40639c")), Some(LegendSymbol::Stroke(2.0))];
        let g = legend_grid(&[70.0, 90.0], &symbols, 11.0, 0, 8.0);
        assert_eq!(g.size, 11.0, "mixed legend's cell collapses to fontSize: {}", g.size);
        assert_eq!(g.box_h, 12.0, "row height is the square entry's own 12, not 13: {}", g.box_h);
        assert_eq!(g.row_y, vec![0.0]);
    }

    #[test]
    fn a_square_and_circle_legend_keeps_the_uniform_cell() {
        let symbols = [Some(LegendSymbol::Square("#40639c")), Some(LegendSymbol::Circle)];
        let g = legend_grid(&[70.0, 90.0], &symbols, 11.0, 0, 8.0);
        assert_eq!(g.size, 12.0, "{}", g.size);
    }

    /// A circle ringed in the bars' border is a point layer's legend glyph beside
    /// a bar's square: unlike an arc's stroked circle it keeps the uniform cell.
    #[test]
    fn a_square_and_a_bordered_circle_keep_the_uniform_cell_a_stroked_circle_collapses() {
        let size = |circle| {
            legend_grid(&[70.0, 90.0], &[Some(LegendSymbol::Square("#fff")), Some(circle)], 11.0, 0, 8.0).size
        };
        assert_eq!(size(LegendSymbol::BorderedCircle("#fff")), 12.0);
        assert_eq!(size(LegendSymbol::StrokedCircle("#fff")), 11.0);
    }

    #[test]
    fn the_short_stroke_glyph_stays_one_stroke_tall_in_a_vertical_legend() {
        let box_h = |symbol| legend_grid(&[70.0, 90.0], &[Some(symbol), Some(symbol)], 11.0, 1, 8.0).box_h;
        assert!(box_h(LegendSymbol::ShortStroke) < box_h(LegendSymbol::Square("#40639c")));
    }

    /// A `stroke` glyph's horizontal half-extent is `10 + strokeWidth`
    /// (the fixed 20px segment `M-10,0L10,0`, expanded by Vega's FULL
    /// `boundStroke` — same expansion the square glyph gets), not its
    /// vertical half `strokeWidth / 2`. Probed via `vega_to_scenegraph` on
    /// `dual-y-axis-lab/revenue_vs_conversion`: entry groups `translate(0,0)`
    /// (width 82.0888671875) and `translate(100,0.75)` (a `stroke` glyph,
    /// `stroke-width="2"`) — column pitch `100 = columnPadding 10 +
    /// ceil(6.5) + ceil(82.0888671875)`, where the placed column's own
    /// `x1 = c − (10 + 2) = 5.5 − 12 = −6.5`, not the base entry's.
    #[test]
    fn mixed_mark_legend_column_pitch_reads_the_stroke_glyphs_own_bound() {
        let symbols = [Some(LegendSymbol::Square("#40639c")), Some(LegendSymbol::Stroke(2.0))];
        let label_ws = [63.0888671875, 79.2236328125];
        let g = legend_grid(&label_ws, &symbols, 11.0, 0, 8.0);
        assert_eq!(g.col_x, vec![0.0, 100.0], "column pitch matches the reference translate: {:?}", g.col_x);
    }

    /// A single-series `line` base admits a shared-scale `line` layer, so
    /// entry 0 CAN carry the `Stroke` glyph too (`libs/chart-svg/AGENTS.md`'s
    /// line-base row) — `x1` must be read from the column being placed, not
    /// hardcoded to entry 0.
    #[test]
    fn mixed_mark_legend_column_pitch_when_the_stroke_glyph_is_the_base_entry() {
        let symbols = [Some(LegendSymbol::Stroke(2.0)), Some(LegendSymbol::Square("#40639c"))];
        let g = legend_grid(&[50.0, 50.0], &symbols, 11.0, 0, 8.0);
        assert_eq!(g.col_x, vec![0.0, 80.0], "column pitch reads column 1's own square bound: {:?}", g.col_x);
    }

    /// Probe-pinned against `vl_convert`'s scenegraph for a `[0, 156]`
    /// zero-anchored size scale (`range: [4, 361]`) at its d3 5-tick set
    /// `0, 20, …, 140`: row pitch grows 14, 14, 16, 18, 20, 21, 23 as the
    /// symbols grow, box height 147 (title band 17 on top makes the
    /// reference's own 164), and the uniform column x lands at the LARGEST
    /// entry's own cell half (10, cell 20) regardless of a smaller row's
    /// own extent.
    #[test]
    fn size_legend_grid_matches_vega() {
        let sizes = [
            4.0,
            49.76923076923077,
            95.53846153846153,
            141.30769230769232,
            187.07692307692307,
            232.84615384615387,
            278.61538461538464,
            324.3846153846154,
        ];
        let label_ws = [4.0; 8]; // widths are irrelevant to row_y/box_h/symbol_c
        let g = size_legend_grid(&sizes, &label_ws, 11.0, 8.0, (1.0, 1.0));
        assert_eq!(g.row_y, vec![0.0, 14.0, 28.0, 44.0, 62.0, 82.0, 103.0, 126.0]);
        assert_eq!(g.box_h, 147.0);
        assert_eq!((g.symbol_c, g.label_x), (10.0, 28.0));
        assert_eq!(g.row_c[0], 5.5, "smallest entry's own cell half: {:?}", g.row_c);
        assert_eq!(g.row_c[7], 10.0, "largest entry's own cell half: {:?}", g.row_c);
        assert!(size_legend_grid(&[], &[], 11.0, 8.0, (1.0, 1.0)).row_y.is_empty());
    }

    /// An entry group's top edge is its own origin, so a symbol's ink above it
    /// adds no height: the 338.7-size entry (r 9.202) of production's size
    /// legend is `ceil(10 + 9.202 + 1.5) = 21` tall, probed on
    /// `fixtures/boards/multiples-legend-layers/06`.
    #[test]
    fn a_size_legend_row_is_as_tall_as_its_lowest_entry_bottom() {
        let (_, box_h, _) = size_legend_row(&[3.14, 338.7], &[4.0, 4.0], 11.0, 8.0, (1.0, 1.0));
        assert_eq!(box_h, 21.0);
    }

    /// Hand-derived from `gridLayout`'s column law for two entries at an 11px
    /// font, 8px label offset, 4px labels: sizes 4 and 324.38 give cells 11
    /// and 20 (centers 5.5 and 10); the first entry's right bound is
    /// `max(8, 19 + 4) = 23`, the second's left bound is -0.505, so the second
    /// column starts at `23 + 10 + 1 = 34`; the tallest entry's bottom is
    /// `10 + 9.0 + 1.5 = 20.51`, so the row is 21 tall, each entry centered in it.
    #[test]
    fn size_legend_row_lays_entries_out_as_columns() {
        let (cells, box_h, width) = size_legend_row(&[4.0, 324.3846153846154], &[4.0, 4.0], 11.0, 8.0, (1.0, 1.0));
        assert_eq!((box_h, width), (21.0, 66.0));
        assert_eq!((cells[0].symbol_x, cells[0].label_x, cells[0].c), (5.5, 19.0, 5.5));
        assert_eq!((cells[1].symbol_x, cells[1].label_x, cells[1].c), (44.0, 62.0, 10.0));
        assert!((cells[0].y - 5.25).abs() < 1e-9 && (cells[1].y - 0.2465).abs() < 1e-3, "{:?}", (cells[0].y, cells[1].y));
    }

    /// `clamp(unit width, 100, 200)`: the floor holds below 100, the unit width
    /// passes through between the bounds, the ceiling holds above 200.
    #[test]
    fn a_horizontal_gradient_is_the_unit_width_clamped_to_100_and_200() {
        let probed = [(50.0, 100.0), (64.0, 100.0), (100.0, 100.0), (150.0, 150.0), (199.0, 199.0), (200.0, 200.0), (240.0, 200.0), (300.0, 200.0)];
        for (unit, length) in probed {
            assert_eq!(horizontal_gradient_length("top", false, unit), length, "unit width {unit}");
        }
    }

    // Real font family present in InterVariable.ttf; "sans-serif" is skipped by face_for().
    const TEST_FONT: &str = "'Inter Variable', Inter, sans-serif";

    fn test_fonts() -> crate::text::FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        crate::text::FontBook::load_dir(&dir).expect("test fonts load")
    }

    /// `legendParams` (vega-view-transforms `layout/legend.js:41-64`) for a
    /// start-anchored strip: a side legend reads `xBounds`, a top or bottom
    /// one `yBounds`; the far edge floors and the legend ends `offset` short
    /// of it (`column`/`row: End`), the near edge ceils and the legend
    /// starts `offset` past it.
    #[test]
    fn legend_origin_anchors_each_edge_on_its_own_bounds() {
        let (xb, yb, size) = ((-40.6, 300.2), (-3.5, 251.1), (60.0, 45.0));
        let plot = (300.0, 250.0);
        let strip = |edge: &str| LegendPosition { edge: edge.into(), align: LegendAlign::Start, overlay: false };
        assert_eq!(legend_origin(&strip("left"), xb, yb, plot, size), (-41.0 - 18.0 - 60.0, 0.0));
        assert_eq!(legend_origin(&strip("right"), xb, yb, plot, size), (301.0 + 18.0, 0.0));
        assert_eq!(legend_origin(&strip("top"), xb, yb, plot, size), (0.0, -4.0 - 18.0 - 45.0));
        assert_eq!(legend_origin(&strip("bottom"), xb, yb, plot, size), (0.0, 252.0 + 18.0));
    }

    fn legend_cfg(direction: &str, columns: Option<u32>) -> LegendConfig {
        serde_json::from_value(serde_json::json!({
            "visible": true, "position": {"edge": "right", "align": "start", "overlay": false},
            "direction": direction, "columns": columns,
            "label": {"font": {"family": TEST_FONT, "size": 11.0}, "padding": 8.0},
            "title": {"font": {"family": TEST_FONT}, "visible": false},
        }))
        .unwrap()
    }

    /// `entryColumns` (vega-parser `guides/guide-util.js:23-26`): the
    /// authored count, else one column for a vertical legend and every entry
    /// in one row (0) for a horizontal one. Production forwards only a
    /// positive count (`legend_to_vl`, `vl_field_maps.py:1050-1051`).
    #[test]
    fn entry_columns_reads_the_authored_count_else_the_direction() {
        assert_eq!(entry_columns(&legend_cfg("vertical", Some(3))), 3);
        assert_eq!(entry_columns(&legend_cfg("horizontal", Some(2))), 2);
        assert_eq!(entry_columns(&legend_cfg("vertical", None)), 1);
        assert_eq!(entry_columns(&legend_cfg("vertical", Some(0))), 1);
        assert_eq!(entry_columns(&legend_cfg("horizontal", None)), 0);
        assert_eq!(entry_columns(&legend_cfg("horizontal", Some(0))), 0);
    }

    /// A vertical legend's entries sort by `[index % ceil(n / columns),
    /// index]` (`legend-symbol-groups.js:140-144`) before `gridLayout` places
    /// them row-major (vega-view-transforms `layout/grid.js:99-100`); a
    /// horizontal one keeps legend order (`:146-151`). Four entries over
    /// three columns make two rows, so the sort fills two columns while the
    /// grid stays three wide: slot order 0, 2, 1 then 3.
    #[test]
    fn a_vertical_legend_over_columns_fills_column_major() {
        let fonts = test_fonts();
        let labels = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"];
        let entries: Vec<LegendEntry> =
            labels.iter().map(|label| LegendEntry {
                label,
                fill: "#000",
                symbol: None,
                series_key: None,
            }).collect();
        let order = |direction: &str, columns: u32, n: usize| {
            vega_legend(&entries[..n], "", &legend_cfg(direction, Some(columns)), &fonts).unwrap().order
        };
        assert_eq!(order("vertical", 2, 5), vec![0, 3, 1, 4, 2]);
        assert_eq!(order("vertical", 3, 5), vec![0, 2, 4, 1, 3]);
        assert_eq!(order("vertical", 3, 4), vec![0, 2, 1, 3]);
        assert_eq!(order("horizontal", 2, 5), vec![0, 1, 2, 3, 4]);
        let legend = vega_legend(&entries, "", &legend_cfg("vertical", Some(2)), &fonts).unwrap();
        assert_eq!((legend.grid.col_x.len(), legend.grid.row_y.len()), (2, 3));
        let mut svg = String::new();
        emit_vega_legend(&mut svg, &entries, &legend, &legend_cfg("vertical", Some(2)), LegendSymbol::Circle, 0.0, 0.0, false);
        let label_at = |label: &str| {
            let tag = &svg[..svg.find(&format!(">{label}</text>")).unwrap()];
            let tag = &tag[tag.rfind("<text").unwrap()..];
            let attr = |name: &str| {
                let rest = &tag[tag.find(&format!(" {name}=\"")).unwrap() + name.len() + 3..];
                rest[..rest.find('"').unwrap()].parse::<f64>().unwrap()
            };
            (attr("x"), attr("y"))
        };
        let grid = &legend.grid;
        // Delta (index 3) heads the second column; Gamma (index 2) ends the first.
        assert_eq!(label_at("Delta").0, label_at("Alpha").0 + grid.col_x[1]);
        assert_eq!(label_at("Delta").1, label_at("Alpha").1);
        assert_eq!(label_at("Gamma").0, label_at("Alpha").0);
        assert_eq!(label_at("Gamma").1, label_at("Alpha").1 + grid.row_y[2] - grid.row_y[0]);
        let drawn: Vec<usize> = labels.iter().map(|l| svg.find(&format!(">{l}</text>")).unwrap()).collect();
        assert!(drawn[0] < drawn[3] && drawn[3] < drawn[1], "drawn in slot order: {drawn:?}");
    }

    /// A layered chart's legend mixes symbols — the bar base keeps its own
    /// square swatch, the line layer's entry paints a stroke circle, even
    /// though both come off the SAME `entries` slice and one shared-symbol
    /// `emit_vega_legend` call (`_mixed_mark_legend_symbols`,
    /// `_overlay.py:649`).
    #[test]
    fn entry_symbol_override_paints_its_own_glyph_not_the_shared_default() {
        let cfg: LegendConfig = serde_json::from_value(serde_json::json!({
            "visible": true, "position": {"edge": "top", "align": "start", "overlay": false}, "direction": "horizontal",
            "label": {"font": {"family": TEST_FONT, "size": 11.0}, "padding": 8.0},
            "title": {"font": {"family": TEST_FONT}, "visible": false},
        }))
        .unwrap();
        let entries = [
            LegendEntry {
                label: "actual",
                fill: "#40639c",
                symbol: Some(LegendSymbol::Square("#fff")),
                series_key: None,
            },
            LegendEntry {
                label: "target",
                fill: "#dea628",
                symbol: Some(LegendSymbol::Circle),
                series_key: None,
            },
        ];
        let fonts = test_fonts();
        let legend = vega_legend(&entries, "", &cfg, &fonts).unwrap();
        let mut svg = String::new();
        emit_vega_legend(&mut svg, &entries, &legend, &cfg, LegendSymbol::Square("#fff"), 0.0, 0.0, false);
        assert!(svg.contains("<rect"), "base entry keeps its square: {svg}");
        assert!(svg.contains("A5,5,0,1,1"), "layer entry paints a circle: {svg}");
    }

    /// Vega's legend groups (`role-legend` > title + one `role-legend-entry` >
    /// a `role-legend-symbol` and a `role-legend-label` per entry): the
    /// thumbnail strips legends by `role-legend` and the hover runtime skips
    /// a cursor inside one.
    fn assert_vega_legend_roles(legend_svg: &str, labels: &[&str]) {
        let svg = format!("<svg>{legend_svg}</svg>");
        let doc = roxmltree::Document::parse(&svg).unwrap();
        let has = |n: roxmltree::Node, class: &str| {
            n.attribute("class").is_some_and(|c| c.split_whitespace().any(|t| t == class))
        };
        let root: Vec<_> = doc.root_element().children().filter(|n| n.is_element()).collect();
        assert_eq!(root.len(), 1, "{svg}");
        assert!(has(root[0], "role-legend"), "{svg}");
        let title = root[0].children().find(|n| has(*n, "role-legend-title")).expect("a title group");
        assert_eq!(title.descendants().find(|n| n.has_tag_name("text")).and_then(|t| t.text()), Some("Segment"));
        let entries: Vec<_> = root[0].children().filter(|n| has(*n, "role-legend-entry")).collect();
        assert_eq!(entries.len(), 1, "{svg}");
        let symbols = entries[0].children().filter(|n| has(*n, "role-legend-symbol")).count();
        let painted: Vec<_> = entries[0]
            .children()
            .filter(|n| has(*n, "role-legend-label"))
            .map(|n| n.descendants().find(|t| t.has_tag_name("text")).and_then(|t| t.text()).unwrap())
            .collect();
        assert_eq!((symbols, painted.as_slice()), (labels.len(), labels), "{svg}");
    }

    fn titled_entries() -> [LegendEntry<'static>; 2] {
        ["Enterprise", "SMB"].map(|label| LegendEntry { label, fill: "#40639c", symbol: None, series_key: None })
    }

    #[test]
    fn a_vega_legend_draws_vegas_legend_groups() {
        let mut cfg = legend_cfg("vertical", None);
        cfg.title.visible = Some(true);
        let entries = titled_entries();
        let legend = vega_legend(&entries, "Segment", &cfg, &test_fonts()).unwrap();
        let mut svg = String::new();
        emit_vega_legend(&mut svg, &entries, &legend, &cfg, LegendSymbol::Circle, 0.0, 0.0, false);
        assert_vega_legend_roles(&svg, &["Enterprise", "SMB"]);
    }

    #[test]
    fn a_vertical_margin_legend_draws_vegas_legend_groups() {
        let mut svg = String::new();
        emit_vertical_legend(&mut svg, &titled_entries(), "Segment", &legend_cfg("vertical", None), 0.0, 0.0, false);
        assert_vega_legend_roles(&svg, &["Enterprise", "SMB"]);
    }
}
