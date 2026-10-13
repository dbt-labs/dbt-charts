//! Board body text — transliteration of `render/prose.py` (card-grid
//! flow), `render/column_packer.py` (span geometry and the penalty-driven
//! column packer), and `boards.py`'s fence-masked Jinja resolution.
//!
//! The text's own mdsvg `Style`/`FontSlots` are seamed from production the
//! same way the title's are (see `title.rs`'s module doc) — `body_text_
//! font_family`/`get_compact_style` are called for real in Python and
//! forwarded, never re-derived here. So is the run's grid: `plan_board_prose`
//! is a resolve-time decision, read off `ResolvedBoard.prose_plan`.


use mdsvg::fonts::{FontSlot, FontSlots};
use mdsvg::style::Style;
use serde::Deserialize;
use serde_json::Value;

use crate::board::{px, translate_group};
use crate::common::Divider;
use crate::embed_fonts::{record_painted_italics, PaintedItalics};
use crate::svg::num_full;

// Readable cap on the measure inside a span, in characters per line
// (`markdown_style.py::MAX_CHARS`).
const MAX_CHARS: i64 = 75;

// column_packer.py's own hard-coded engine internals (Dave, 2026-08-10) —
// not exposed to dbt_charts.yml.
const MIN_LINES_PER_COLUMN: i64 = 5;
const FORBIDDEN: f64 = f64::INFINITY;
const ORPHAN_PENALTY: f64 = 3000.0;
const WIDOW_PENALTY: f64 = 3000.0;
const INTERIOR_BREAK_PENALTY: f64 = 1.0;
const BLOCK_BREAK_PENALTY: f64 = 0.0;
const BALANCE_SCALE: f64 = 100.0;
const BALANCE_UNDERSHOOT: f64 = 0.35;

// One sentence of ordinary English prose, used to calibrate character width
// (`markdown_style.py::_PROSE_SAMPLE`) — OS/2 xAvgCharWidth runs too wide.
const PROSE_SAMPLE: &str = "North America's primary issue is insufficient MQL supply, not a regional Stage 0 target miss or a broad loss of selling capacity.";

/// `compile/models/style/theme/board.py::TextStyle`'s column knobs.
pub struct TextDoc {
    pub max_number: Option<i64>,
    pub max_chars: Option<i64>,
    /// `column.divider` — the vertical line between prose columns.
    pub divider: Divider,
}

/// `compile/models/board/normalized.py::ProsePlan` — the card grid one run of
/// stacked prose sits on. Production chooses it once per run at resolve time
/// (`prose.py::plan_board_prose`) and serializes it on `ResolvedBoard`, so
/// both the sizing and the render pass read it here rather than re-plan.
/// `snap` (`normalized.py:323`) is board-scoped: some drawn prose block lays
/// out in two or more columns at its real slot width, so a lone column keeps
/// to its span instead of widening.
#[derive(Deserialize, Clone, Copy)]
pub struct ProsePlan {
    pub grid: i64,
    pub board_width: f64,
    pub gap: f64,
    pub snap: bool,
}

/// `markdown_style.py::_prose_char_ratio`: mean character width of real
/// prose, as a fraction of font size, measured against the SAME font slot
/// the renderer paints with (mdsvg's own HVAR-exact measurer, not chart-
/// renderer's simpler `text::FontBook`).
fn prose_char_ratio(regular: &FontSlot) -> f64 {
    let measurer =
        mdsvg::fonts::cached_measurer(&regular.path, regular.font_number, regular.weight);
    measurer.measure(PROSE_SAMPLE, 1.0) / PROSE_SAMPLE.chars().count() as f64
}

/// `markdown_style.py::max_chars_to_px`.
fn max_chars_to_px(regular: &FontSlot, font_size: f64, max_chars: i64) -> f64 {
    prose_char_ratio(regular) * font_size * max_chars as f64
}

/// Python's float `//` (`float_floor_div`): floor of the exact quotient,
/// which `(a / b).floor()` misses when the division rounds up onto an integer.
fn py_floor_div(a: f64, b: f64) -> f64 {
    let rem = a % b;
    let mut div = (a - rem) / b;
    if rem != 0.0 && ((b < 0.0) != (rem < 0.0)) {
        div -= 1.0;
    }
    let floor = div.floor();
    if div - floor > 0.5 {
        floor + 1.0
    } else {
        floor
    }
}

/// `column_packer.py::grid_span`: width of one span of the `grid`-up card
/// row across `board_width`.
fn grid_span(board_width: f64, grid: i64, gap: f64) -> f64 {
    (board_width - (grid - 1) as f64 * gap) / grid as f64
}

/// `column_packer.py::span_boxes`: the `(x, width)` boxes a container
/// `container` wide offers prose, on the board grid's own offsets.
fn span_boxes(container: f64, plan: &ProsePlan) -> Vec<(f64, f64)> {
    let span = grid_span(plan.board_width, plan.grid, plan.gap);
    let slack = (plan.gap / 2.0).max(1.0);
    let fits = py_floor_div(container + plan.gap + slack, span + plan.gap) as i64;
    let count = fits.min(plan.grid).max(1);
    if count == 1 {
        return vec![(0.0, container)];
    }
    (0..count)
        .map(|i| {
            let x = i as f64 * (span + plan.gap);
            (x, span.min(container - x))
        })
        .collect()
}

/// `column_packer.py::columns_earned`: most columns, up to `spans`, that
/// each earn `MIN_LINES_PER_COLUMN`.
fn columns_earned(total_lines: i64, spans: i64) -> i64 {
    spans
        .min(total_lines.div_euclid(MIN_LINES_PER_COLUMN))
        .max(1)
}

/// `render/prose.py::_measure` over `markdown_style.py::readable_measure`:
/// width the text wraps at inside a span.
fn capped_measure(span_text: f64, max_chars: Option<i64>, char_px: f64) -> f64 {
    span_text.min(max_chars.unwrap_or(MAX_CHARS) as f64 * char_px)
}

/// `render/prose.py::_group_spans`: merge `boxes` into equal groups of
/// `size` consecutive spans.
fn group_spans(boxes: &[(f64, f64)], size: usize) -> Vec<(f64, f64)> {
    (0..=boxes.len() - size)
        .step_by(size)
        .map(|i| {
            let last = boxes[i + size - 1];
            (boxes[i].0, last.0 + last.1 - boxes[i].0)
        })
        .collect()
}

/// `render/prose.py::_fit_tables`: fewest spans per column, and the measure,
/// that hold every table unwrapped.
fn fit_tables(
    renderer: &mdsvg::renderer::SVGRenderer,
    tables: &[mdsvg::types::AnyBlock],
    boxes: &[(f64, f64)],
    pad: f64,
    max_chars: Option<i64>,
    char_px: f64,
    slot: f64,
) -> Result<(Vec<(f64, f64)>, f64), String> {
    let height = |table: &mdsvg::types::AnyBlock, width: f64| -> Result<f64, String> {
        Ok(renderer.measure_blocks(&vec![table.clone()], width)?[0].line_advance)
    };
    let unwrapped = tables
        .iter()
        .map(|t| height(t, slot))
        .collect::<Result<Vec<_>, _>>()?;
    let text_width =
        |group: &[(f64, f64)]| group.iter().map(|b| b.1).fold(f64::INFINITY, f64::min) - 2.0 * pad;

    for capped in [true, false] {
        for size in 1..=boxes.len() {
            let group = group_spans(boxes, size);
            let mut width = text_width(&group);
            if capped {
                width = capped_measure(width, max_chars, char_px);
            }
            let mut holds = true;
            for (table, natural) in tables.iter().zip(&unwrapped) {
                if height(table, width)? > *natural {
                    holds = false;
                    break;
                }
            }
            if holds {
                return Ok((group, width));
            }
        }
    }
    let group = group_spans(boxes, boxes.len());
    let width = text_width(&group);
    Ok((group, width))
}

/// `render/prose.py::_measure_offset`: left offset of the measure block
/// within its card.
fn measure_offset(align: mdsvg::style::TextAlign, available: f64, used: f64) -> f64 {
    let slack = (available - used).max(0.0);
    match align {
        mdsvg::style::TextAlign::Center => slack / 2.0,
        mdsvg::style::TextAlign::Right => slack,
        mdsvg::style::TextAlign::Left => 0.0,
    }
}

/// `column_packer.py::PackUnit`: one placeable line, or one whole
/// unsplittable block.
#[derive(Clone, Copy)]
struct PackUnit {
    advance: f64,
    space_before: f64,
    block_id: usize,
    line_index: i64,
    line_total: i64,
    splittable: bool,
    keep_with_next: bool,
}

impl PackUnit {
    fn height(&self) -> f64 {
        self.advance + self.space_before
    }
}

/// `column_packer.py::_break_penalty`: cost of breaking the column
/// immediately after `units[i]`.
fn break_penalty(units: &[PackUnit], i: usize) -> f64 {
    if i >= units.len() - 1 {
        return FORBIDDEN; // nothing follows; not a real break point
    }
    let here = units[i];
    let nxt = units[i + 1];

    // Never split an unsplittable block, and never separate a heading from
    // the opening of the text it introduces.
    if here.block_id == nxt.block_id && !here.splittable {
        return FORBIDDEN;
    }
    if here.keep_with_next {
        return FORBIDDEN;
    }
    if i >= 1 && units[i - 1].keep_with_next {
        return FORBIDDEN; // only one line would follow the heading
    }

    if here.block_id != nxt.block_id {
        return BLOCK_BREAK_PENALTY;
    }

    // Same paragraph: how many of its lines fall either side of the break?
    let before = here.line_index + 1;
    let after = here.line_total - before;
    if before == 1 {
        return ORPHAN_PENALTY;
    }
    if after == 1 {
        return WIDOW_PENALTY;
    }
    INTERIOR_BREAK_PENALTY
}

/// `column_packer.py::pack_columns`: assign each unit to a column index,
/// minimizing total cost. Returns a vec parallel to `units`; assignments
/// are non-decreasing.
fn pack_columns(units: &[PackUnit], columns: i64) -> Vec<usize> {
    let n = units.len();
    if n == 0 {
        return Vec::new();
    }
    if columns <= 1 {
        return vec![0; n];
    }
    let columns = columns as usize;

    let mut prefix = vec![0.0_f64; n + 1];
    for (i, u) in units.iter().enumerate() {
        prefix[i + 1] = prefix[i] + u.height();
    }
    let target = prefix[n] / columns as f64;

    let penalty: Vec<f64> = (0..n).map(|i| break_penalty(units, i)).collect();

    // best[k][i] = min cost of placing units[:i] into k columns.
    let inf = f64::INFINITY;
    let mut best = vec![vec![inf; n + 1]; columns + 1];
    let mut take = vec![vec![0usize; n + 1]; columns + 1];
    best[0][0] = 0.0;

    for k in 1..=columns {
        for i in 1..=n {
            // column k spans units[j:i]
            for j in (k - 1)..i {
                let prior = best[k - 1][j];
                if prior == inf {
                    continue;
                }
                let brk = if j == 0 { 0.0 } else { penalty[j - 1] };
                if brk == inf {
                    continue;
                }
                let height = prefix[i] - prefix[j];
                let deviation = height - target;
                let normalizer = target * target;
                let mut cost = prior + brk;
                if normalizer > 0.0 {
                    let earliness = (columns - k + 1) as f64;
                    let lopsided = if deviation < 0.0 {
                        1.0 + BALANCE_UNDERSHOOT * earliness
                    } else {
                        1.0
                    };
                    cost += lopsided * BALANCE_SCALE * (deviation * deviation) / normalizer;
                }
                if cost < best[k][i] {
                    best[k][i] = cost;
                    take[k][i] = j;
                }
            }
        }
    }

    // Recover the split. If every arrangement was forbidden, fall back to
    // an even division rather than failing.
    if best[columns][n] == inf {
        let per = n.div_ceil(columns);
        return (0..n).map(|i| (i / per).min(columns - 1)).collect();
    }

    let mut bounds = vec![n];
    let mut i = n;
    for k in (1..=columns).rev() {
        i = take[k][i];
        bounds.push(i);
    }
    bounds.reverse();

    let mut assignment = vec![0usize; n];
    for (col, window) in bounds.windows(2).enumerate() {
        assignment[window[0]..window[1]].fill(col);
    }
    assignment
}

/// `render/prose.py::_column_divider_line`: a vertical SVG line between
/// prose columns.
fn column_divider_line(x: f64, height: f64, divider: &Divider) -> String {
    // Snap x to integer: a 1px structural mark at fractional x blurs
    // horizontally.
    let x_snapped = num_full(px(x));
    let h_s = num_full(height);
    let sw_s = num_full(divider.width);
    format!(
        r#"<line x1="{x_snapped}" y1="0" x2="{x_snapped}" y2="{h_s}" stroke="{}" stroke-width="{sw_s}"{}/>"#,
        mdsvg::utils::escape_xml(&divider.color),
        divider.dash_attr()
    )
}

/// `render/prose.py::render_prose_svg`. `width` is the slot the text may
/// occupy, already inset by `card_padding`; the container whose spans it
/// takes is `width + 2 * card_padding`. `sink` is the paint site's own
/// (`embed_fonts::record_painted_italics`); the prose is recorded against the
/// family it was laid out in, `style.font_family`.
#[allow(clippy::too_many_arguments)]
pub fn render_prose_svg(
    markdown: &str,
    width: f64,
    col: &TextDoc,
    plan: &ProsePlan,
    card_padding: f64,
    style: Style,
    fonts: FontSlots,
    allow_raw_html: bool,
    sink: Option<&PaintedItalics>,
) -> Result<(String, f64), String> {
    let align = style.text_align;
    let family = style.font_family.clone();
    let char_px = max_chars_to_px(&fonts.regular, style.base_font_size, 1);

    let blocks = mdsvg::parser::parse(markdown);
    // `html_policy: trusted-raw` (`allow_raw_html`) is the one tier where
    // production renders raw HTML via `foreignObject` and nh3 instead of
    // painting its source as escaped text (`boards.py`'s
    // `_render_text_svg(..., allow_raw_html=...)`); `none` and `safe-subset`
    // both reach mdsvg's escaped-paragraph arm. That render is not ported, and
    // mdsvg would paint the escaped form for it too, so the refusal is named
    // here, tied to the policy, at every block depth.
    if allow_raw_html && mdsvg::types::contains_raw_html(&blocks) {
        return Err("board text raw HTML not modeled (html_policy: trusted-raw)".to_string());
    }
    // Board prose never reads an image's source for a size (`prose.py`
    // passes `fetch_image_sizes=False`), so an image is sized by its authored
    // dims or the style alone.
    let renderer = mdsvg::renderer::SVGRenderer::new(style, fonts)?.with_fetch_image_sizes(false);

    let pad = card_padding;
    let mut boxes = span_boxes(width + 2.0 * pad, plan);
    let tables: Vec<mdsvg::types::AnyBlock> = blocks
        .iter()
        .filter(|b| matches!(b, mdsvg::types::AnyBlock::Table(_)))
        .cloned()
        .collect();
    let mut measure = if tables.is_empty() {
        // Columns share one measure, so a box a hair short of its span sets it.
        let narrowest = boxes.iter().map(|b| b.1).fold(f64::INFINITY, f64::min);
        capped_measure(narrowest - 2.0 * pad, col.max_chars, char_px)
    } else {
        let (fitted, measure) =
            fit_tables(&renderer, &tables, &boxes, pad, col.max_chars, char_px, width)?;
        boxes = fitted;
        measure
    };
    let spans = boxes.len() as i64;

    // Every column is equal width, so a block wraps identically wherever it
    // lands — the line list is sliced, never re-flowed.
    let metrics = renderer.measure_blocks(&blocks, measure)?;
    let ceiling = col.max_number.map_or(spans, |m| spans.min(m));
    let total_lines: i64 = metrics.iter().map(|m| m.line_count as i64).sum();
    let n_cols = columns_earned(total_lines, ceiling);
    if n_cols == 1 && tables.is_empty() && !plan.snap {
        // `prose.py:268-281`: with no multi-column prose on the board, a lone
        // column wraps at least as wide as sizing asked for, so a line that
        // fits stays one line; once it outgrows its span it sits in the whole
        // card. Where some block does go multi-column (`snap`), lone ones keep
        // to their span so every edge on the board is a span edge.
        let wanted = renderer
            .max_content_width(&blocks)?
            .min(capped_measure(width, col.max_chars, char_px));
        if wanted > measure {
            measure = wanted;
            boxes = vec![(0.0, width + 2.0 * pad)];
        }
    }

    // Alignment belongs to the span the reader sees, not to a width the
    // renderer chose.
    let insets: Vec<f64> = boxes
        .iter()
        .map(|&(box_x, box_width)| box_x + measure_offset(align, box_width - 2.0 * pad, measure))
        .collect();

    if n_cols <= 1 {
        let result = renderer.render_content(&blocks, measure, 0.0)?;
        let inset = insets[0];
        let w = num_full(width);
        let h = num_full(result.height);
        let body = if inset == 0.0 {
            result.content()
        } else {
            format!(
                r#"<g transform="translate({}, 0)">{}</g>"#,
                num_full(px(inset)),
                result.content()
            )
        };
        let svg = format!(
            r#"<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">{body}</svg>"#
        );
        record_painted_italics(sink, &family, &renderer.used_fonts());
        return Ok((svg, result.height));
    }

    let mut units: Vec<PackUnit> = Vec::new();
    let mut origin: Vec<(usize, i64)> = Vec::new(); // (block index, line index) per unit
    for (block_index, m) in metrics.iter().enumerate() {
        for line_index in 0..m.line_count as i64 {
            units.push(PackUnit {
                advance: m.line_advance,
                space_before: if line_index == 0 { m.space_before } else { 0.0 },
                block_id: block_index,
                line_index,
                line_total: m.line_count as i64,
                splittable: m.splittable,
                keep_with_next: m.keep_with_next,
            });
            origin.push((block_index, line_index));
        }
    }

    let assignment = pack_columns(&units, n_cols);

    // Collapse per-line assignments back into one window per (column, block).
    let mut windows: Vec<Vec<(usize, i64, i64)>> = vec![Vec::new(); n_cols as usize];
    for (unit_index, &column) in assignment.iter().enumerate() {
        let (block_index, line_index) = origin[unit_index];
        let current = &mut windows[column];
        match current.last_mut() {
            Some(last) if last.0 == block_index => last.2 = line_index,
            _ => current.push((block_index, line_index, line_index)),
        }
    }

    // Taken off the first rendered window rather than the renderer's private
    // accessor: mdsvg is published separately, so only its public surface is
    // a contract we can hold it to.
    let mut style_block = String::new();
    let mut column_parts: Vec<String> = Vec::new();
    let mut column_heights: Vec<f64> = Vec::new();

    for (ci, window) in windows.iter().enumerate() {
        let mut y = 0.0_f64;
        let mut col_elements: Vec<String> = Vec::new();
        for (position, &(block_index, first, last)) in window.iter().enumerate() {
            let m = metrics[block_index];
            // A block opening a column sits at the top: both the gap that
            // would separate it from preceding text and the whitespace it
            // draws above itself collapse, exactly as they do at the top of
            // the board.
            let opens_column = position == 0;
            if !opens_column {
                y += m.space_before;
            }
            let lift = if opens_column { m.leading_margin } else { 0.0 };
            let count = (last - first + 1) as usize;
            let rendered = renderer.render_block_window(
                &blocks[block_index],
                measure,
                first as usize,
                Some(count),
            )?;
            if style_block.is_empty() {
                style_block = rendered.style_block.clone();
            }
            col_elements.push(translate_group(
                "0",
                &num_full(px(y - lift)),
                &rendered.elements,
                "",
            ));
            y += count as f64 * m.line_advance - lift;
        }
        column_heights.push(y);
        if !col_elements.is_empty() {
            column_parts.push(translate_group(
                &num_full(px(insets[ci])),
                "0",
                &col_elements.join(""),
                "",
            ));
        }
    }

    let actual_col_height = column_heights.iter().cloned().fold(0.0_f64, f64::max);

    if col.divider.visible {
        for &(x, box_width) in &boxes[..(n_cols - 1) as usize] {
            // Midway through the card gap between this span's box and the
            // next; boxes are container-relative and the slot starts `pad`
            // inside it.
            column_parts.push(column_divider_line(
                x + box_width + plan.gap / 2.0 - pad,
                actual_col_height,
                &col.divider,
            ));
        }
    }

    let w = num_full(width);
    let h = num_full(actual_col_height);
    let columns_svg = column_parts.join("");
    let svg = format!(
        r#"<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">{style_block}{columns_svg}</svg>"#
    );
    record_painted_italics(sink, &family, &renderer.used_fonts());
    Ok((svg, actual_col_height))
}

/// `render/sizing.py::get_markdown_text_height`: renders (discarding the
/// SVG) to get the flowed height — production genuinely renders body text
/// twice, once during sizing and once at final composition, same as a
/// board title. Jinja resolution here is NOT fence-masked
/// (`resolve_jinja_template` directly, unlike `_render_text_svg`'s own
/// `_resolve_markdown_jinja`) — a real production asymmetry, not a port gap.
#[allow(clippy::too_many_arguments)]
pub fn get_markdown_text_height(
    text: &str,
    variables: &serde_json::Map<String, Value>,
    width: f64,
    col: &TextDoc,
    plan: &ProsePlan,
    card_padding: f64,
    style: Style,
    fonts: FontSlots,
    allow_raw_html: bool,
) -> Result<f64, String> {
    if text.is_empty() {
        return Ok(0.0);
    }
    let resolved = crate::title::resolve_jinja(text, variables)?;
    Ok(render_prose_svg(
        &resolved,
        width,
        col,
        plan,
        card_padding,
        style,
        fonts,
        allow_raw_html,
        None,
    )?
    .1)
}

/// `markdown_fence.py::resolve_outside_fences`: fence-masked Jinja resolution — the
/// path `_render_text_svg` actually draws with (see `get_markdown_text_height`
/// for the un-masked sizing-pass sibling).
pub fn resolve_markdown_jinja(
    text: &str,
    variables: &serde_json::Map<String, Value>,
) -> Result<String, String> {
    mdsvg::markdown_fence::resolve_outside_fences(text, |masked| {
        crate::title::resolve_jinja(masked, variables)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolve_markdown_jinja_substitutes_outside_fences_only() {
        let mut variables = serde_json::Map::new();
        variables.insert("x".to_string(), Value::String("hi".to_string()));
        let text = "{{ x }}\n```\n{{ x }}\n```\n";
        let resolved = resolve_markdown_jinja(text, &variables).unwrap();
        assert_eq!(resolved, "hi\n```\n{{ x }}\n```\n");
    }

}
