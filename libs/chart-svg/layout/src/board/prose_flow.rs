//! How one text block is measured and flowed across its card grid: `render/prose.py`'s `_measure`,
//! `_group_spans`, `_fit_tables`, `_Stretch`, `_spanning`, `_a_later_column_opens_with_a_heading`,
//! `_pack_units`, `_measured` and `_columns_of`, over `column_packer`'s packer. The board's plan
//! ([`super::prose_plan`]) and the paint ([`super::prose::text`]) both read a block through
//! [`columns_of`], so the width a grid is chosen at is the width the text is wrapped at.

use diagnostic::{refuse, Diagnostic};
use mdsvg::renderer::{BlockMetrics, SVGRenderer};
use mdsvg::types::{AnyBlock, Document};

use crate::board::column_packer::{pack_earned, PackUnit};
use crate::board::prose::span_boxes;
use crate::markdown_style::readable_measure;

/// The card grid a run of prose divides its board by: `ProsePlan` less its `snap`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Grid {
    /// Thirds (3), halves (2) or one whole-board span (1).
    pub count: usize,
    /// The root board's content width.
    pub board_width: f64,
    /// `card_row_gap`: the gap the board's cards are pitched by.
    pub gap: f64,
}

/// A text block and what it is measured in.
pub struct Text {
    pub blocks: Document,
    pub renderer: SVGRenderer,
    pub pad: f64,
    /// `max_chars_to_px(regular, size, 1)`: one character of the body face.
    pub char_px: f64,
    pub max_chars: Option<u32>,
    pub max_number: Option<u32>,
}

impl Text {
    /// `_measure`: the width the text wraps at inside a span. The cap trims the measure, not the span,
    /// so every left edge stays on a card content edge.
    pub fn measure(&self, span_text: f64) -> f64 {
        span_text.min(readable_measure(self.char_px, self.max_chars))
    }

    /// `measure_blocks`, with the one shape the measurer refuses (a line an inline image makes
    /// taller than the rest) named as a refusal.
    pub fn metrics(&self, blocks: &Document, width: f64) -> Result<Vec<BlockMetrics>, Diagnostic> {
        self.renderer.measure_blocks(blocks, width).map_err(|message| {
            if message.starts_with("mdsvg-rs: inline image taller than its line") {
                Diagnostic::refused(refuse!("a board's text with an inline image taller than its line is not supported"))
            } else {
                crate::markdown_style::mdsvg_error(message)
            }
        })
    }
}

/// One run of blocks `first` up to `stop` of a text that lay out alike. Flowing, `assignment` gives each
/// of `units` its column; spanning, the blocks sit across the columns and both are empty.
pub struct Stretch {
    pub first: usize,
    pub stop: usize,
    pub spanning: bool,
    pub units: Vec<PackUnit>,
    pub assignment: Vec<usize>,
}

impl Stretch {
    pub fn n_cols(&self) -> usize {
        self.assignment.iter().max().map_or(0, |last| *last) + 1
    }
}

/// `_columns_of`'s answer: the boxes, prose measure, table box and measure, metrics and stretches of
/// one block.
pub struct Columns {
    pub boxes: Vec<(f64, f64)>,
    pub measure: f64,
    pub table_box: (f64, f64),
    pub table_measure: Option<f64>,
    pub metrics: Vec<BlockMetrics>,
    pub stretches: Vec<Stretch>,
}

/// `_group_spans`: `boxes` merged into equal groups of `size` consecutive spans.
fn group_spans(boxes: &[(f64, f64)], size: usize) -> Vec<(f64, f64)> {
    (0..=boxes.len() - size)
        .step_by(size)
        .map(|i| {
            let (last_x, last_width) = boxes[i + size - 1];
            (boxes[i].0, last_x + last_width - boxes[i].0)
        })
        .collect()
}

/// `_fit_tables`: the fewest spans per column, and the table measure, that hold every table unwrapped.
/// A candidate narrower than the tables' min-content width is never taken, since height is not
/// monotone below it; candidates are the whole-span groups of the run's grid, at the capped prose
/// measure first and at the group's full width only if no capped measure holds the tables.
fn fit_tables(text: &Text, tables: &[AnyBlock], boxes: &[(f64, f64)], slot: f64) -> Result<(Vec<(f64, f64)>, f64), Diagnostic> {
    let pad = text.pad;
    let height = |table: &AnyBlock, width: f64| -> Result<f64, Diagnostic> {
        Ok(text.metrics(&vec![table.clone()], width)?[0].line_advances[0])
    };
    // A lone span caps prose, not tables: a table wider than the span keeps the card's slot.
    let whole = [(0.0, slot + 2.0 * pad)];
    let boxes = if boxes.len() == 1 { &whole[..] } else { boxes };
    let unwrapped = tables.iter().map(|table| height(table, slot)).collect::<Result<Vec<_>, _>>()?;
    let floor = text.renderer.min_content_width(tables).map_err(crate::markdown_style::mdsvg_error)?;
    let text_width = |group: &[(f64, f64)]| group.iter().map(|b| b.1).fold(f64::INFINITY, f64::min) - 2.0 * pad;
    for capped in [true, false] {
        for size in 1..=boxes.len() {
            let group = group_spans(boxes, size);
            let width = if capped { text.measure(text_width(&group)) } else { text_width(&group) };
            if width < floor {
                continue;
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

/// `_spanning`: which blocks sit across the columns rather than flow through them. A table only when it
/// is wider than a column; one that fits is one more unsplittable block of the flow. A heading goes with
/// what is under it, wherever the flow would have left it: the table it titles, or at the end of the
/// text, the next row.
pub fn spanning(blocks: &[AnyBlock], wide_tables: bool) -> Vec<bool> {
    let mut opens = true;
    let mut marks: Vec<bool> = blocks
        .iter()
        .map(|block| {
            opens = (wide_tables && matches!(block, AnyBlock::Table(_))) || (opens && matches!(block, AnyBlock::Heading(_)));
            opens
        })
        .collect();
    for index in (0..blocks.len()).rev() {
        let led = index + 1 == blocks.len() || marks[index + 1];
        if led && matches!(blocks[index], AnyBlock::Heading(_)) {
            marks[index] = true;
        }
    }
    marks
}

fn a_later_column_opens_with_a_heading(stretch: &Stretch, blocks: &[AnyBlock]) -> bool {
    let mut seen = std::collections::HashSet::new();
    stretch
        .units
        .iter()
        .zip(&stretch.assignment)
        .any(|(unit, &column)| seen.insert(column) && column > 0 && matches!(blocks[unit.block_id], AnyBlock::Heading(_)))
}

fn pack_units(metrics: &[BlockMetrics], first: usize, stop: usize) -> Vec<PackUnit> {
    (first..stop)
        .flat_map(|block_id| {
            let m = &metrics[block_id];
            m.line_advances.iter().enumerate().map(move |(line_index, &advance)| PackUnit {
                advance,
                space_before: if line_index == 0 { m.space_before } else { m.item_gap },
                block_id,
                line_index,
                line_total: m.line_advances.len(),
                splittable: m.splittable,
                keep_with_next: m.keep_with_next,
                leading_margin: if line_index == 0 { m.column_lift } else { 0.0 },
                whole_item: m.whole_items,
            })
        })
        .collect()
}

/// `_measured`: metrics at `measure`, tables at their own, and the stretches they flow into.
pub fn measured(text: &Text, measure: f64, table_measure: Option<f64>, ceiling: usize) -> Result<(Vec<BlockMetrics>, Vec<Stretch>), Diagnostic> {
    let blocks = &text.blocks;
    // Every column is equal width, so a block wraps identically wherever it lands: the line list is
    // sliced, never re-flowed.
    let mut metrics = text.metrics(blocks, measure)?;
    if let Some(table_measure) = table_measure.filter(|&t| t != measure) {
        let at_table_width = text.metrics(blocks, table_measure)?;
        for (i, block) in blocks.iter().enumerate() {
            if matches!(block, AnyBlock::Table(_)) {
                metrics[i] = at_table_width[i].clone();
            }
        }
    }
    let line_height = text.renderer.style.base_font_size * text.renderer.style.line_height;
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
    // Sections flowing side by side are peers: the first heading joins them on the one baseline rather
    // than titling columns that open with titles of their own.
    if stretches.len() > 1
        && stretches[0].spanning
        && blocks[..stretches[0].stop].iter().all(|b| matches!(b, AnyBlock::Heading(_)))
        && a_later_column_opens_with_a_heading(&stretches[1], blocks)
    {
        marks[..stretches[0].stop].fill(false);
        stretches = stretches_of(&marks);
    }
    Ok((metrics, stretches))
}

/// `_columns_of`: how many columns a block earns at its real slot `width`. Tables alone take the width
/// that holds them unwrapped; every other block keeps the span's reading measure, so a wide table never
/// stretches its paragraphs.
pub fn columns_of(text: &Text, width: f64, grid: Grid) -> Result<Columns, Diagnostic> {
    let boxes = span_boxes(width + 2.0 * text.pad, grid.board_width, grid.count, grid.gap);
    // Columns share one measure, so a box a hair short of its span sets it.
    let narrowest = boxes.iter().map(|b| b.1).fold(f64::INFINITY, f64::min);
    let measure = text.measure(narrowest - 2.0 * text.pad);
    let tables: Vec<AnyBlock> = text.blocks.iter().filter(|b| matches!(b, AnyBlock::Table(_))).cloned().collect();
    let mut table_box = boxes[0];
    let mut table_measure = None;
    if !tables.is_empty() {
        let (group, fitted) = fit_tables(text, &tables, &boxes, width)?;
        table_box = group[0];
        table_measure = Some(fitted);
    }
    let ceiling = text.max_number.map_or(boxes.len(), |n| boxes.len().min(n as usize));
    let (metrics, stretches) = measured(text, measure, table_measure, ceiling)?;
    Ok(Columns { boxes, measure, table_box, table_measure, metrics, stretches })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_heading_goes_with_the_wide_table_under_it() {
        let blocks = mdsvg::parse("Body.\n\n## Results\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n## Next\n\nMore.");
        assert_eq!(spanning(&blocks, true), vec![false, true, true, true, false]);
        assert_eq!(spanning(&blocks, false), vec![false; 5]);
    }

    #[test]
    fn a_heading_ending_the_text_goes_with_the_next_row() {
        let blocks = mdsvg::parse("Body.\n\n## Results");
        assert_eq!(spanning(&blocks, false), vec![false, true]);
    }
}
