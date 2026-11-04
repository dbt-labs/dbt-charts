//! Board body text — transliteration of `render/prose.py` (card-grid
//! flow), `render/column_packer.py` (span geometry and the penalty-driven
//! column packer), and `boards.py`'s fence-masked Jinja resolution.
//!
//! The text's own mdsvg `Style`/`FontSlots` are seamed from production the
//! same way the title's are (see `title.rs`'s module doc) — `body_text_
//! font_family`/`get_compact_style` are called for real in Python and
//! forwarded, never re-derived here. So is the run's grid: `plan_board_prose`
//! is a resolve-time decision, read off `ResolvedBoard.prose_plan`.

use mdsvg::style::Style;
use measure::fonts::{FontSlot, FontSlots};
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
    /// `style.text.font.case` — applied to the words of every block.
    pub case: Option<String>,
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
/// renderer's simpler `measure::FontBook`).
fn prose_char_ratio(regular: &FontSlot) -> f64 {
    let measurer =
        measure::fonts::cached_measurer(&regular.path, regular.font_number, regular.weight);
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
        Ok(renderer.measure_blocks(&vec![table.clone()], width)?[0].line_advances[0])
    };
    let unwrapped = tables
        .iter()
        .map(|t| height(t, slot))
        .collect::<Result<Vec<_>, _>>()?;
    let floor = renderer.table_min_content_width(tables)?;
    let text_width =
        |group: &[(f64, f64)]| group.iter().map(|b| b.1).fold(f64::INFINITY, f64::min) - 2.0 * pad;

    for capped in [true, false] {
        for size in 1..=boxes.len() {
            let group = group_spans(boxes, size);
            let mut width = text_width(&group);
            if capped {
                width = capped_measure(width, max_chars, char_px);
            }
            let mut holds = width >= floor;
            for (table, natural) in tables.iter().zip(&unwrapped) {
                if !holds || height(table, width)? > *natural {
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
/// unsplittable block. A list item is one whole unit too, but the units of a
/// list may part.
#[derive(Clone, Copy)]
struct PackUnit {
    advance: f64,
    space_before: f64,
    block_id: usize,
    line_index: i64,
    line_total: i64,
    splittable: bool,
    keep_with_next: bool,
    leading_margin: f64,
    whole_item: bool,
}

impl PackUnit {
    fn height(&self) -> f64 {
        self.advance + self.space_before
    }

    /// `PackUnit.collapse`: height this unit sheds by opening a column.
    fn collapse(&self) -> f64 {
        self.space_before + self.leading_margin
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

    if here.block_id != nxt.block_id {
        return BLOCK_BREAK_PENALTY;
    }
    if here.whole_item {
        return INTERIOR_BREAK_PENALTY;
    }
    if i >= 1 && units[i - 1].keep_with_next {
        return FORBIDDEN; // only one line of the block would follow the heading
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
/// minimizing total cost. Returns a vec parallel to `units`, or `None` when
/// every arrangement breaks somewhere forbidden; assignments are
/// non-decreasing.
fn pack_columns(units: &[PackUnit], columns: i64) -> Option<Vec<usize>> {
    let n = units.len();
    if n == 0 {
        return Some(Vec::new());
    }
    if columns <= 1 {
        return Some(vec![0; n]);
    }
    let columns = columns as usize;

    let mut prefix = vec![0.0_f64; n + 1];
    for (i, u) in units.iter().enumerate() {
        prefix[i + 1] = prefix[i] + u.height();
    }
    let target = (prefix[n] - units[0].collapse()) / columns as f64;

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
                let height = prefix[i] - prefix[j] - units[j].collapse();
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

    if best[columns][n] == inf {
        return None;
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
    Some(assignment)
}

/// `column_packer.py::_painted_heights`: height drawn in each column of
/// `assignment`.
fn painted_heights(units: &[PackUnit], assignment: &[usize], columns: usize) -> Vec<f64> {
    let mut heights = vec![0.0_f64; columns];
    for (i, (unit, &column)) in units.iter().zip(assignment).enumerate() {
        let opens = i == 0 || assignment[i - 1] != column;
        heights[column] += unit.height() - if opens { unit.collapse() } else { 0.0 };
    }
    heights
}

/// `column_packer.py::pack_earned`: pack into the most columns, up to
/// `spans`, that each earn the line floor. The total only bounds the count:
/// a list parts between items and a code block not at all, so a column can
/// land short of what an even division promises. One column is always
/// available.
fn pack_earned(units: &[PackUnit], spans: i64, line_height: f64) -> Vec<usize> {
    let heights: Vec<f64> = units.iter().map(PackUnit::height).collect();
    let total = d3rs::py_float::py_fsum(&heights) - units[0].collapse();
    let earned = columns_earned((total / line_height).round_ties_even() as i64, spans);
    for columns in (2..=earned).rev() {
        if let Some(assignment) = pack_columns(units, columns) {
            if painted_heights(units, &assignment, columns as usize)
                .iter()
                .all(|height| (height / line_height).round_ties_even() as i64 >= MIN_LINES_PER_COLUMN)
            {
                return assignment;
            }
        }
    }
    vec![0; units.len()]
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
        print::xml_escape_attr(&divider.color),
        divider.dash_attr()
    )
}

/// `prose.py::_Stretch`: blocks `first` up to `stop` of a block's text, which
/// lay out alike. Flowing, `assignment` gives each of `units` its column;
/// spanning, the blocks sit across the columns and both are empty.
struct Stretch {
    first: usize,
    stop: usize,
    spanning: bool,
    units: Vec<PackUnit>,
    assignment: Vec<usize>,
}

impl Stretch {
    fn n_cols(&self) -> i64 {
        self.assignment.iter().max().map_or(0, |&c| c as i64) + 1
    }
}

/// `prose.py::_spanning`: which blocks sit across the columns rather than flow
/// through them. A table only when it is wider than a column; one that fits is
/// one more unsplittable block of the flow. A heading goes with what is under
/// it, wherever the flow would have left it: the table it titles, or at the end
/// of the text, the next row.
fn spanning(blocks: &[mdsvg::types::AnyBlock], wide_tables: bool) -> Vec<bool> {
    use mdsvg::types::AnyBlock::{Heading, Table};
    let mut opens = true;
    let mut marks: Vec<bool> = blocks
        .iter()
        .map(|block| {
            opens = (wide_tables && matches!(block, Table(_))) || (opens && matches!(block, Heading(_)));
            opens
        })
        .collect();
    for index in (0..marks.len()).rev() {
        let led = index + 1 == marks.len() || marks[index + 1];
        if led && matches!(blocks[index], Heading(_)) {
            marks[index] = true;
        }
    }
    marks
}

/// `prose.py::_a_later_column_opens_with_a_heading`.
fn a_later_column_opens_with_a_heading(stretch: &Stretch, blocks: &[mdsvg::types::AnyBlock]) -> bool {
    let mut seen = std::collections::HashSet::new();
    stretch.units.iter().zip(&stretch.assignment).any(|(unit, &column)| {
        seen.insert(column) && column > 0 && matches!(blocks[unit.block_id], mdsvg::types::AnyBlock::Heading(_))
    })
}

fn pack_units(metrics: &[mdsvg::renderer::BlockMetrics], first: usize, stop: usize) -> Vec<PackUnit> {
    (first..stop)
        .flat_map(|block_id| {
            let m = &metrics[block_id];
            m.line_advances.iter().enumerate().map(move |(line_index, &advance)| PackUnit {
                advance,
                space_before: if line_index == 0 { m.space_before } else { m.item_gap },
                block_id,
                line_index: line_index as i64,
                line_total: m.line_advances.len() as i64,
                splittable: m.splittable,
                keep_with_next: m.keep_with_next,
                leading_margin: if line_index == 0 { m.column_lift } else { 0.0 },
                whole_item: m.whole_items,
            })
        })
        .collect()
}

/// `prose.py::_measured`: metrics at `measure`, tables at their own, and the
/// stretches they flow into.
fn measured(
    blocks: &[mdsvg::types::AnyBlock],
    renderer: &mdsvg::renderer::SVGRenderer,
    measure: f64,
    table_measure: Option<f64>,
    ceiling: i64,
) -> Result<(Vec<mdsvg::renderer::BlockMetrics>, Vec<Stretch>), String> {
    // Every column is equal width, so a block wraps identically wherever it
    // lands — the line list is sliced, never re-flowed.
    let mut metrics = renderer.measure_blocks(&blocks.to_vec(), measure)?;
    if let Some(table_measure) = table_measure.filter(|&t| t != measure) {
        let at_table_width = renderer.measure_blocks(&blocks.to_vec(), table_measure)?;
        for (i, block) in blocks.iter().enumerate() {
            if matches!(block, mdsvg::types::AnyBlock::Table(_)) {
                metrics[i] = at_table_width[i].clone();
            }
        }
    }
    let line_height = renderer.style.base_font_size * renderer.style.line_height;
    let stretches_of = |marks: &[bool]| -> Vec<Stretch> {
        let mut stretches = Vec::new();
        let mut first = 0;
        while first < blocks.len() {
            let stop = (first..blocks.len()).find(|&i| marks[i] != marks[first]).unwrap_or(blocks.len());
            if marks[first] {
                stretches.push(Stretch { first, stop, spanning: true, units: Vec::new(), assignment: Vec::new() });
            } else {
                let units = pack_units(&metrics, first, stop);
                let assignment = pack_earned(&units, ceiling, line_height);
                stretches.push(Stretch { first, stop, spanning: false, units, assignment });
            }
            first = stop;
        }
        stretches
    };
    let mut marks = spanning(blocks, table_measure.is_some_and(|t| t > measure));
    let mut stretches = stretches_of(&marks);
    // Sections flowing side by side are peers: the first heading joins them on the
    // one baseline rather than titling columns that open with titles of their own.
    if stretches.len() > 1
        && stretches[0].spanning
        && blocks[..stretches[0].stop].iter().all(|b| matches!(b, mdsvg::types::AnyBlock::Heading(_)))
        && a_later_column_opens_with_a_heading(&stretches[1], blocks)
    {
        marks[..stretches[0].stop].fill(false);
        stretches = stretches_of(&marks);
    }
    Ok((metrics, stretches))
}

/// How one text block is measured and flowed: what both its paint and its first
/// baseline are asked of (`prose.py::_ProseFlow`).
struct ProseFlow {
    renderer: mdsvg::renderer::SVGRenderer,
    blocks: mdsvg::types::Document,
    boxes: Vec<(f64, f64)>,
    pad: f64,
    measure: f64,
    table_box: (f64, f64),
    table_measure: Option<f64>,
    metrics: Vec<mdsvg::renderer::BlockMetrics>,
    stretches: Vec<Stretch>,
    n_cols: i64,
    /// Width of a heading: the columns it titles, or the one measure.
    spanning_measure: f64,
}

impl ProseFlow {
    /// `_ProseFlow.paints_whole`: one column at one width draws in one piece,
    /// without ever flowing.
    fn paints_whole(&self) -> bool {
        self.n_cols <= 1 && self.table_measure.is_none_or(|t| t == self.measure)
    }
}

#[allow(clippy::too_many_arguments)]
fn prose_flow(
    markdown: &str,
    width: f64,
    col: &TextDoc,
    plan: &ProsePlan,
    card_padding: f64,
    style: Style,
    fonts: FontSlots,
    allow_raw_html: bool,
    heading_case: Option<&str>,
) -> Result<ProseFlow, String> {
    let char_px = max_chars_to_px(&fonts.regular, style.base_font_size, 1);

    let mut blocks = mdsvg::parser::parse(markdown);
    if let Some(case) = &col.case {
        crate::title::case_blocks(&mut blocks, case)?;
    }
    crate::title::style_prose_headings(&mut blocks, heading_case, &style)?;
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
    let renderer = mdsvg::renderer::SVGRenderer::new(style, fonts)?;

    let pad = card_padding;
    let mut boxes = span_boxes(width + 2.0 * pad, plan);
    // Columns share one measure, so a box a hair short of its span sets it.
    let narrowest = boxes.iter().map(|b| b.1).fold(f64::INFINITY, f64::min);
    let mut measure = capped_measure(narrowest - 2.0 * pad, col.max_chars, char_px);
    let tables: Vec<mdsvg::types::AnyBlock> = blocks
        .iter()
        .filter(|b| matches!(b, mdsvg::types::AnyBlock::Table(_)))
        .cloned()
        .collect();
    let mut table_box = boxes[0];
    let mut table_measure = None;
    if !tables.is_empty() {
        let (group, fitted) = fit_tables(&renderer, &tables, &boxes, pad, col.max_chars, char_px, width)?;
        table_box = group[0];
        table_measure = Some(fitted);
    }
    let spans = boxes.len() as i64;
    let ceiling = col.max_number.map_or(spans, |m| spans.min(m));
    let (mut metrics, mut stretches) = measured(&blocks, &renderer, measure, table_measure, ceiling)?;

    if !plan.snap && stretches.iter().all(|s| s.n_cols() == 1) {
        // `prose.py:_prose_flow`: with no multi-column prose on the board, a
        // lone column wraps at least as wide as sizing asked for, so a line
        // that fits stays one line; once it outgrows its span it sits in the
        // whole card. Where some block does go multi-column (`snap`), lone ones
        // keep to their span so every edge on the board is a span edge.
        let wanted = renderer
            .max_content_width(&blocks)?
            .min(capped_measure(width, col.max_chars, char_px));
        if wanted > measure {
            measure = wanted;
            boxes = vec![(0.0, width + 2.0 * pad)];
            table_box = boxes[0];
            table_measure = table_measure.map(|t| t.max(measure));
            // Without a table of another width the block paints in one piece
            // and never reads its metrics.
            if table_measure.is_some_and(|t| t != measure) {
                (metrics, stretches) = measured(&blocks, &renderer, measure, table_measure, 1)?;
            }
        }
    }

    // A heading wraps at the columns it titles, so it is measured there too.
    let n_cols = stretches.iter().map(Stretch::n_cols).max().unwrap_or(1);
    let mut spanning_measure = measure;
    if n_cols > 1 {
        let (last_x, last_width) = boxes[n_cols as usize - 1];
        spanning_measure = last_x + last_width - boxes[0].0 - 2.0 * pad;
        let at_span = renderer.measure_blocks(&blocks, spanning_measure)?;
        for stretch in stretches.iter().filter(|s| s.spanning) {
            for i in stretch.first..stretch.stop {
                if matches!(blocks[i], mdsvg::types::AnyBlock::Heading(_)) {
                    metrics[i] = at_span[i].clone();
                }
            }
        }
    }

    Ok(ProseFlow {
        renderer,
        blocks,
        boxes,
        pad,
        measure,
        table_box,
        table_measure,
        metrics,
        stretches,
        n_cols,
        spanning_measure,
    })
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
    heading_case: Option<&str>,
    sink: Option<&PaintedItalics>,
) -> Result<(String, f64), String> {
    let align = style.text_align;
    let family = style.font_family.clone();
    let flow = prose_flow(markdown, width, col, plan, card_padding, style, fonts, allow_raw_html, heading_case)?;
    let (renderer, blocks, boxes, pad, measure, metrics) =
        (&flow.renderer, &flow.blocks, &flow.boxes, flow.pad, flow.measure, &flow.metrics);
    let n_cols = flow.n_cols;
    let table_measure = flow.table_measure;

    // Alignment belongs to the span the reader sees, not to a width the
    // renderer chose.
    let insets: Vec<f64> = boxes
        .iter()
        .map(|&(box_x, box_width)| box_x + measure_offset(align, box_width - 2.0 * pad, measure))
        .collect();

    if flow.paints_whole() {
        let result = renderer.render_content(blocks, measure, 0.0)?;
        let inset = insets[0];
        let w = num_full(width);
        let h = num_full(result.height);
        let body = if inset == 0.0 {
            print::print_content(&result.content())
        } else {
            format!(
                r#"<g transform="translate({}, 0)">{}</g>"#,
                num_full(px(inset)),
                print::print_content(&result.content())
            )
        };
        let svg = format!(
            r#"<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">{body}</svg>"#
        );
        record_painted_italics(sink, &family, &renderer.used_fonts());
        return Ok((svg, result.height));
    }

    // Taken off the first rendered window: mdsvg's public surface is the contract.
    let style_block = std::cell::RefCell::new(String::new());
    let window = |block: &mdsvg::types::AnyBlock, block_width: f64, start: usize, count: Option<usize>| {
        let rendered = renderer.render_block_window(block, block_width, start, count)?;
        if style_block.borrow().is_empty() {
            *style_block.borrow_mut() = print::print_content(&mdsvg::indented(std::slice::from_ref(&rendered.style)));
        }
        Ok::<String, String>(print::print_content(&mdsvg::indented(&rendered.elements)))
    };

    let flowed = |stretch: &Stretch| -> Result<(String, f64), String> {
        let stretch_cols = stretch.n_cols();
        let mut windows: Vec<Vec<(usize, i64, i64)>> = vec![Vec::new(); stretch_cols as usize];
        for (unit, &column) in stretch.units.iter().zip(&stretch.assignment) {
            let current = &mut windows[column];
            match current.last_mut() {
                Some(last) if last.0 == unit.block_id => last.2 = unit.line_index,
                _ => current.push((unit.block_id, unit.line_index, unit.line_index)),
            }
        }

        let mut parts: Vec<String> = Vec::new();
        let mut heights: Vec<f64> = Vec::new();
        for (ci, column) in windows.iter().enumerate() {
            let mut y = 0.0_f64;
            let mut elements: Vec<String> = Vec::new();
            for (position, &(block_index, first, last)) in column.iter().enumerate() {
                let m = &metrics[block_index];
                // A block opening a column sits at the top: both the gap that
                // would separate it from preceding text and the whitespace it
                // draws above itself collapse, and a heading rises to the first
                // baseline the other columns open on.
                let opens_column = position == 0;
                if !opens_column {
                    y += m.space_before;
                }
                let lift = if opens_column { m.column_lift } else { 0.0 };
                elements.push(translate_group(
                    "0",
                    &num_full(px(y - lift)),
                    &window(&blocks[block_index], measure, first as usize, Some((last - first + 1) as usize))?,
                    "",
                ));
                y += d3rs::py_float::py_fsum(&m.line_advances[first as usize..=last as usize])
                    + m.item_gap * (last - first) as f64
                    - lift;
            }
            heights.push(y);
            parts.push(translate_group(&num_full(px(insets[ci])), "0", &elements.join(""), ""));
        }

        let height = heights.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
        if col.divider.visible {
            for &(x, box_width) in &boxes[..stretch_cols as usize - 1] {
                // Midway through the card gap between this span's box and the
                // next; boxes are container-relative and the slot starts `pad`
                // inside it.
                parts.push(column_divider_line(x + box_width + plan.gap / 2.0 - pad, height, &col.divider));
            }
        }
        Ok((parts.join(""), height))
    };

    let table_x = table_measure.map_or(0.0, |t| {
        flow.table_box.0 + measure_offset(align, flow.table_box.1 - 2.0 * pad, t)
    });
    let spanning_measure = flow.spanning_measure;
    let heading_x = boxes[0].0
        + measure_offset(align, (boxes[0].1 - 2.0 * pad).max(spanning_measure), spanning_measure);

    let mut y = 0.0_f64;
    let mut painted: Vec<String> = Vec::new();
    for stretch in &flow.stretches {
        if !stretch.spanning {
            y += metrics[stretch.first].space_before;
            let (columns, height) = flowed(stretch)?;
            painted.push(translate_group("0", &num_full(px(y)), &columns, ""));
            y += height;
            continue;
        }
        for index in stretch.first..stretch.stop {
            let (block, m) = (&blocks[index], &metrics[index]);
            y += m.space_before;
            let lift = if index == 0 { m.leading_margin } else { 0.0 };
            let (x, block_width) = match (block, table_measure) {
                (mdsvg::types::AnyBlock::Table(_), Some(t)) => (table_x, t),
                (mdsvg::types::AnyBlock::Table(_), None) => unreachable!("a table spans only past the measure, so it has one"),
                _ => (heading_x, spanning_measure),
            };
            painted.push(translate_group(
                &num_full(px(x)),
                &num_full(px(y - lift)),
                &window(block, block_width, 0, None)?,
                "",
            ));
            y += d3rs::py_float::py_fsum(&m.line_advances) - lift;
        }
    }

    let w = num_full(width);
    let h = num_full(y);
    let svg = format!(
        r#"<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">{}{}</svg>"#,
        style_block.borrow(),
        painted.join("")
    );
    record_painted_italics(sink, &family, &renderer.used_fonts());
    Ok((svg, y))
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
    heading_case: Option<&str>,
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
        heading_case,
        None,
    )?
    .1)
}

/// `prose.py::prose_first_baseline`: how far below a text block's top edge its
/// first baseline sits, when the text opens with a heading (`None` when it opens
/// with anything else). A heading that opens a text card is that card's title: a
/// cols row puts it on the baseline its neighbors' titles share. Asked of the
/// flow that draws it, at the measure the prose is drawn at.
#[allow(clippy::too_many_arguments)]
pub fn prose_first_baseline(
    markdown: &str,
    width: f64,
    col: &TextDoc,
    plan: &ProsePlan,
    card_padding: f64,
    style: Style,
    fonts: FontSlots,
    allow_raw_html: bool,
    heading_case: Option<&str>,
) -> Result<Option<f64>, String> {
    let flow = prose_flow(markdown, width, col, plan, card_padding, style, fonts, allow_raw_html, heading_case)?;
    if !matches!(flow.blocks.first(), Some(mdsvg::types::AnyBlock::Heading(_))) {
        return Ok(None);
    }
    if flow.paints_whole() || flow.stretches[0].spanning {
        return flow.renderer.first_baseline(&flow.blocks, flow.spanning_measure).map(Some);
    }
    let m = &flow.metrics[0];
    let baseline = flow.renderer.first_baseline(&flow.blocks, flow.measure)?;
    Ok(Some(baseline - (m.column_lift - m.leading_margin)))
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

    fn line(block_id: usize, line_index: i64, line_total: i64, advance: f64) -> PackUnit {
        PackUnit {
            advance,
            space_before: 0.0,
            block_id,
            line_index,
            line_total,
            splittable: true,
            keep_with_next: false,
            leading_margin: 0.0,
            whole_item: false,
        }
    }

    fn items(heights: &[f64]) -> Vec<PackUnit> {
        heights
            .iter()
            .enumerate()
            .map(|(i, &h)| PackUnit { whole_item: true, ..line(7, i as i64, heights.len() as i64, h) })
            .collect()
    }

    fn heading(block_id: usize, leading_margin: f64) -> PackUnit {
        PackUnit {
            splittable: false,
            keep_with_next: true,
            leading_margin,
            ..line(block_id, 0, 1, 40.0 + leading_margin)
        }
    }

    #[test]
    fn a_heading_goes_with_the_wide_table_under_it() {
        let blocks = mdsvg::parser::parse("Body.\n\n## Results\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n## Next\n\nMore.");
        // The heading after the table spans by the forward pass, as the one before it
        // now does by the backward one.
        assert_eq!(spanning(&blocks, true), vec![false, true, true, true, false]);
        assert_eq!(spanning(&blocks, false), vec![false, false, false, false, false]);
    }

    #[test]
    fn a_heading_ending_the_text_goes_with_the_next_row() {
        let blocks = mdsvg::parser::parse("Body.\n\n## Results");
        assert_eq!(spanning(&blocks, false), vec![false, true]);
    }

    #[test]
    fn a_heading_opening_a_column_costs_only_what_is_drawn() {
        let mut units = vec![heading(0, 40.0)];
        units.extend((0..10).map(|i| line(1, i, 10, 20.0)));
        let assignment = pack_columns(&units, 2).unwrap();
        assert_eq!(assignment.iter().filter(|&&c| c == 0).count() - 1, 4);
        assert_eq!(assignment.iter().filter(|&&c| c == 1).count(), 6);
    }

    #[test]
    fn any_two_list_items_may_part() {
        assert_eq!(pack_columns(&items(&[100.0, 300.0, 40.0]), 2).unwrap(), vec![0, 1, 1]);
        let mut units = vec![heading(0, 40.0)];
        units.extend(items(&[80.0, 120.0]));
        assert_eq!(pack_columns(&units, 2).unwrap(), vec![0, 0, 1]);
    }

    #[test]
    fn a_legal_break_is_not_enough_when_a_column_falls_short() {
        let assignment = pack_earned(&items(&[60.0, 140.0, 140.0, 60.0]), 3, 20.0);
        assert_eq!(assignment.iter().max(), Some(&1));
    }

    #[test]
    fn an_unbreakable_block_is_one_column_whatever_its_height() {
        let block = PackUnit { splittable: false, ..line(0, 0, 1, 400.0) };
        assert_eq!(pack_earned(&[heading(1, 40.0), block], 3, 20.0), vec![0, 0]);
    }

    #[test]
    fn even_lines_keep_every_span_they_earn() {
        let units: Vec<PackUnit> = (0..15).map(|i| line(0, i, 15, 20.0)).collect();
        let assignment = pack_earned(&units, 3, 20.0);
        let sizes: Vec<usize> = (0..3).map(|c| assignment.iter().filter(|&&a| a == c).count()).collect();
        assert_eq!(sizes, vec![5, 5, 5]);
    }

    #[test]
    fn resolve_markdown_jinja_substitutes_outside_fences_only() {
        let mut variables = serde_json::Map::new();
        variables.insert("x".to_string(), Value::String("hi".to_string()));
        let text = "{{ x }}\n```\n{{ x }}\n```\n";
        let resolved = resolve_markdown_jinja(text, &variables).unwrap();
        assert_eq!(resolved, "hi\n```\n{{ x }}\n```\n");
    }

}
