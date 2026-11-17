//! A board's own title and text, measured and drawn through `mdsvg` with the theme's markdown style
//! (`markdown_style.rs`): `title_renderer`/`title_blocks`/`render_title` and `render_prose_svg` for
//! a text flowed across its card grid (`prose.py::_prose_flow`, `_measure_offset`, `_column_divider_line`;
//! the measure and flow are [`super::prose_flow`], the board's plan [`super::prose_plan`]) with
//! `column_packer.py`'s `choose_grid`, `grid_span` and `span_boxes`. The old composer's copies of
//! these laws are `chart-renderer/src/prose.rs` and `title.rs`; the cutover deletes them.
//!
//! A board's title and text arrive filled (the engine fills every drawn board's words first, and the
//! filled words are literal). What this stage does not model is refused by name: images, emphasis in
//! a family with no italic file to measure it against, raw HTML, and a line an inline image makes
//! taller than the rest. The title's and the text's letter cases
//! apply to their words as `title_blocks`, `prose_blocks` and `style_prose_headings` apply them.
//!
//! [`title`] is the root's one heading through `mdsvg`, filled with the theme's title ink, with its
//! measured height, the text's line box, its first baseline and its one-line width. [`text`] is a text
//! at the columns its plan gives it, aligned in its spans. [`content_box`] is `compute_board_content_box`,
//! with the band's height, the inline band's in place of the title's and the controls'.

use board_model::board::BoardBody;
use board_model::primitives::DividerStyle;
use board_model::style::board::TextAlign;
use board_model::style::font::LetterCase;
use board_model::style::style::Style;
use d3rs::py_float::py_fsum;
use diagnostic::{diag, refuse, Diagnostic};
use mdsvg::renderer::{BlockMetrics, SVGRenderer};
use measure::shaped::FontBook;
use mdsvg::types::{AnyBlock, Document, Heading, Span, SpanType};
use regex::Regex;
use scene::{Attrs, Chars, Child, Element, Escape, Node, Role};
use std::sync::LazyLock;

use crate::board::prose_flow::{columns_of, measured, Columns, Stretch, Text};
use crate::board::prose_plan::Plan;
use crate::board::width::IntrinsicWidth;

use crate::markdown_style::{
    case_blocks, case_words, char_ratio, compact_style, font_slots, has_italic, mdsvg_error, readable_measure,
    title_overrides, Overrides,
};

/// `column_packer.MIN_CHARS`: a span narrower than this is not a readable column.
const MIN_CHARS: f64 = 45.0;

/// A root title as drawn: its `<svg>`, the height it reserves and the text's own span inside it.
#[derive(Debug, Clone)]
pub struct TitleBlock {
    pub svg: Node,
    pub height: f64,
    /// `title_line_box`: `(top, height)` of the heading's text inside its block.
    pub line_box: (f64, f64),
    /// `title_baseline_offset`: how far below the block's top its first baseline sits.
    pub baseline: f64,
    /// `title_max_content_width`: the width the title needs to draw on one line.
    pub one_line_width: f64,
}

/// A text band as drawn: the nodes that sit inside its authoring block and the height they reserve.
#[derive(Debug, Clone)]
pub struct TextBlock {
    pub nodes: Vec<Node>,
    pub height: f64,
}

/// Where a board's text sits: the width it wraps in and the plan of the run of prose it belongs to.
#[derive(Debug, Clone, Copy)]
pub struct Slot {
    /// `prose_width`: the content width less the card padding on both sides.
    pub width: f64,
    pub plan: Plan,
}

/// `prose_slot_widths`: the width a board's text wraps in, the content width less the card padding on
/// both sides.
pub fn slot_width(content_width: f64, card_padding: f64) -> f64 {
    (content_width - 2.0 * card_padding).max(1.0)
}

/// A board's text with the style and fonts it is measured in.
#[derive(Clone, Copy)]
pub struct Source<'a> {
    pub style: &'a Style,
    pub text: &'a str,
    pub book: &'a FontBook,
}

/// Whether `text` writes Jinja, which layout reads only once it is filled.
pub fn has_template_syntax(text: &str) -> bool {
    ["{{", "{%", "{#"].iter().any(|syntax| text.contains(syntax))
}

/// An image drawn from the markup alone is a data URI; any other source is a file or a link the host
/// would have to resolve against the board's directory.
fn refuse_image(url: &str, what: &str) -> Result<(), Diagnostic> {
    if url.get(..11).is_some_and(|scheme| scheme.eq_ignore_ascii_case("data:image/")) {
        return Ok(());
    }
    Err(Diagnostic::refused(refuse!("a board's {what} with an image that is not a data URI is not supported")))
}

/// `italic` says whether the family ships an italic file to measure emphasis against ([`has_italic`]).
fn refuse_spans(spans: &[Span], what: &str, italic: bool) -> Result<(), Diagnostic> {
    for span in spans {
        if span.span_type == SpanType::Image {
            refuse_image(span.url.as_deref().unwrap_or_default(), what)?;
        }
        if !italic && (matches!(span.span_type, SpanType::Italic | SpanType::BoldItalic) || span.italic) {
            return Err(Diagnostic::refused(refuse!(
                "a board's {what} with emphasis in a family with no italic file is not supported"
            )));
        }
    }
    Ok(())
}

fn refuse_blocks(blocks: &Document, what: &str, italic: bool) -> Result<(), Diagnostic> {
    for block in blocks {
        match block {
            AnyBlock::Paragraph(p) => refuse_spans(&p.spans, what, italic)?,
            AnyBlock::Heading(h) => refuse_spans(&h.spans, what, italic)?,
            AnyBlock::CodeBlock(_) | AnyBlock::HorizontalRule(_) => {}
            AnyBlock::UnorderedList(list) => refuse_items(&list.items, what, italic)?,
            AnyBlock::OrderedList(list) => refuse_items(&list.items, what, italic)?,
            AnyBlock::ImageBlock(image) => refuse_image(&image.url, what)?,
            AnyBlock::Table(table) => {
                for row in std::iter::once(&table.header).chain(&table.rows) {
                    for cell in &row.cells {
                        refuse_spans(&cell.spans, what, italic)?;
                    }
                }
            }
            // Every theme paints a quote italic, so a family with no italic file would draw it in a width it cannot measure.
            AnyBlock::Blockquote(quote) => {
                refuse_blocks(&quote.blocks, what, italic)?;
                if !italic {
                    return Err(Diagnostic::refused(refuse!(
                        "a board's {what} with a blockquote in a family with no italic file is not supported"
                    )));
                }
            }
            AnyBlock::RawHtmlBlock(_) => {
                return Err(Diagnostic::refused(refuse!(
                    "a board's {what} with raw HTML is not supported"
                )))
            }
        }
    }
    Ok(())
}

fn refuse_items(items: &[mdsvg::types::ListItem], what: &str, italic: bool) -> Result<(), Diagnostic> {
    for item in items {
        refuse_spans(&item.spans, what, italic)?;
        refuse_blocks(&item.children, what, italic)?;
    }
    Ok(())
}

fn renderer(
    book: &FontBook,
    style: &Style,
    overrides: &Overrides,
) -> Result<SVGRenderer, Diagnostic> {
    let md_style = compact_style(style, overrides)?;
    let slots = font_slots(book, &md_style)?;
    SVGRenderer::new(md_style, slots).map_err(mdsvg_error)
}

/// The title as the heading production draws it (`board_title_markdown`: `# title`), with the
/// document it is the only block of.
fn title_blocks(title: &str, italic: bool, case: &LetterCase) -> Result<(Document, Heading), Diagnostic> {
    let mut blocks = mdsvg::parse(&format!("# {title}"));
    let [AnyBlock::Heading(heading)] = blocks.as_mut_slice() else {
        return Err(Diagnostic::refused(refuse!(
            "a board's title that is not one line of text is not supported"
        )));
    };
    case_words(&mut heading.spans, case)?;
    let heading = heading.clone();
    refuse_blocks(&blocks, "title", italic)?;
    Ok((blocks, heading))
}

/// `WidthMeasurer.board_own_width` (`compile/sizing.py`) for the root's title and text: each needs
/// its widest unbreakable word and asks for its one-line width up to the readable line length
/// (`prose_measure`), inside the card padding on both sides. The title is measured in the title
/// renderer (`title_min_content_width`, `title_max_content_width`) and the text in the body's
/// (`prose_content_widths`), both over the blocks the paint pass draws.
pub fn own_width(style: &Style, title: Option<&str>, kind: TitleKind, text: Option<&str>, book: &FontBook) -> Result<IntrinsicWidth, Diagnostic> {
    let body = compact_style(style, &Overrides::PROSE)?;
    let measure = readable_measure(char_ratio(book, &body)? * body.base_font_size, style.text.column.max_chars);
    let inset = 2.0 * style.frame.card_padding;
    let ask = |renderer: &SVGRenderer, blocks: &Document| -> Result<IntrinsicWidth, Diagnostic> {
        let floor = renderer.min_content_width(blocks).map_err(mdsvg_error)?;
        let one_line = renderer.max_content_width(blocks).map_err(mdsvg_error)?;
        Ok(IntrinsicWidth { min: Some(floor), preferred: Some(one_line.min(measure)) }.inset(inset))
    };
    let mut width = IntrinsicWidth::NONE;
    if let Some(title) = title {
        let (blocks, _, renderer) = title_setup(style, title, book, kind)?;
        width = width.combine_max(ask(&renderer, &blocks)?);
    }
    if let Some(text) = text {
        let text = text_setup(style, text, book)?;
        width = width.combine_max(ask(&text.renderer, &text.blocks)?);
    }
    Ok(width)
}

/// What sizing, planning and painting a board's title all read it through (`title_blocks` and
/// `title_renderer`): its heading, and the renderer of the root title's typography.
fn title_setup(style: &Style, title: &str, book: &FontBook, kind: TitleKind) -> Result<(Document, Heading, SVGRenderer), Diagnostic> {
    let overrides = title_overrides(style, kind.level, kind.root)?;
    let italic = has_italic(&compact_style(style, &overrides)?);
    let (blocks, heading) = title_blocks(title, italic, &style.title.font.case)?;
    let renderer = renderer(book, style, &overrides)?;
    Ok((blocks, heading, renderer))
}

/// What sizing, planning and painting a board's text all read it through (`prose_blocks`, the body
/// renderer and `_prose_typography`): the blocks with their small headings bolded, the body's renderer
/// and the card padding, character width and column limits the flow measures with.
pub fn text_setup(style: &Style, source: &str, book: &FontBook) -> Result<Text, Diagnostic> {
    let md_style = compact_style(style, &Overrides::PROSE)?;
    let mut blocks = mdsvg::parse(source);
    refuse_blocks(&blocks, "text", has_italic(&md_style))?;
    case_blocks(&mut blocks, &style.text.font.case)?;
    style_prose_headings(&mut blocks, &style.title.font.case, |level| md_style.get_heading_size(level), md_style.base_font_size)?;
    let char_px = char_ratio(book, &md_style)? * md_style.base_font_size;
    let renderer = renderer(book, style, &Overrides::PROSE)?;
    let column = &style.text.column;
    Ok(Text { blocks, renderer, pad: style.frame.card_padding, char_px, max_chars: column.max_chars, max_number: column.max_number })
}

/// The board's text when it has any: production tests `if board.text:`, so `text: ""` is no text.
pub fn board_text(body: &BoardBody) -> Option<&str> {
    body.text.as_ref().map(|text| text.as_str()).filter(|text| !text.is_empty())
}

/// Which board's title: its semantic level (the count of titled boards from the root down to it) and
/// whether the root's own.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TitleKind {
    pub level: u32,
    pub root: bool,
}

impl TitleKind {
    /// The root board's title, a level-1 heading.
    pub const ROOT: Self = Self { level: 1, root: true };
}

/// `render_title` for a board's title at `width`, with its measured height and line box.
pub fn title(
    style: &Style,
    title: &str,
    book: &FontBook,
    width: f64,
    kind: TitleKind,
) -> Result<TitleBlock, Diagnostic> {
    let (blocks, heading, renderer) = title_setup(style, title, book, kind)?;
    let rendered = renderer
        .render_content(&blocks, width, 0.0)
        .map_err(mdsvg_error)?;
    let line_box = renderer
        .heading_text_box(&heading, width)
        .map_err(mdsvg_error)?;
    let baseline = renderer
        .first_baseline(&blocks, width)
        .map_err(mdsvg_error)?;
    let one_line_width = renderer
        .max_content_width(&blocks)
        .map_err(mdsvg_error)?;
    Ok(TitleBlock {
        svg: paint_fill_svg(rendered.svg(), style.title.font.color.as_str()),
        height: rendered.height,
        line_box,
        baseline,
        one_line_width,
    })
}

/// `_paint_title_svg_fill` (`render/boards.py`; `chart-renderer/src/title.rs` for the old composer): an
/// inline `fill` on every `<text>` that has no `style`, and the fill of every `style` that has one,
/// since a class rule beats a presentation attribute.
fn paint_fill_svg(mut node: Node, color: &str) -> Node {
    if let scene::Shape::Svg { children, .. } = &mut node.shape {
        for child in children.iter_mut() {
            if let scene::Shape::Element(element) = &mut child.shape {
                paint_fill(element, color);
            }
        }
    }
    node
}

/// The greedy `([^"]*\bfill\s*:\s*)[^;"]*` of production's second pass: the last `fill:` in a style,
/// never `fill-opacity`.
static LAST_FILL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"(?s)^(.*\bfill\s*:\s*)[^;"]*(.*)$"#).expect("the fill pattern compiles")
});

fn paint_fill(element: &mut Element, color: &str) {
    let attrs = std::mem::take(&mut element.attrs);
    element.attrs = if element.tag == "text" && attrs.get("style").is_none() {
        Attrs::new()
            .set("style", format!("fill: {color}"))
            .extend(attrs)
    } else {
        attrs.map_values(&["style"], |style| {
            LAST_FILL
                .replace(style, |m: &regex::Captures| {
                    format!("{}{color}{}", &m[1], &m[2])
                })
                .into_owned()
        })
    };
    for child in &mut element.children {
        if let Child::Element(inner) = child {
            paint_fill(inner, color);
        }
    }
}

/// `style_prose_headings`: a top-level heading takes the title's letter case, and one drawn at body
/// size takes the bold weight.
fn style_prose_headings(blocks: &mut Document, case: &LetterCase, sizes: impl Fn(u8) -> f64, body: f64) -> Result<(), Diagnostic> {
    for block in blocks {
        if let AnyBlock::Heading(heading) = block {
            case_words(&mut heading.spans, case)?;
            if sizes(heading.level) <= body {
                for span in &mut heading.spans {
                    if span.span_type == SpanType::Text {
                        span.span_type = SpanType::Bold;
                    }
                }
            }
        }
    }
    Ok(())
}

/// `choose_grid`: 3 (thirds), 2 (halves) or 1 whole-board span.
pub(crate) fn choose_grid(board_width: f64, gap: f64, pad: f64, char_px: f64) -> usize {
    let readable =
        |grid: usize| (grid_span(board_width, grid, gap) - 2.0 * pad) / char_px >= MIN_CHARS;
    if !readable(2) {
        1
    } else if readable(3) {
        3
    } else {
        2
    }
}

pub(crate) fn grid_span(board_width: f64, grid: usize, gap: f64) -> f64 {
    (board_width - (grid - 1) as f64 * gap) / grid as f64
}

/// Python's `a // b` on floats (`math.floor` of the true quotient, as `float.__floordiv__` computes it
/// from `fmod`); `chart-renderer/src/prose.rs` holds the old composer's copy.
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

/// `span_boxes`: the `(x, width)` boxes a container offers prose.
pub(crate) fn span_boxes(container: f64, board_width: f64, grid: usize, gap: f64) -> Vec<(f64, f64)> {
    let span = grid_span(board_width, grid, gap);
    let slack = (gap / 2.0).max(1.0);
    let count = py_floor_div(container + gap + slack, span + gap)
        .min(grid as f64)
        .max(1.0) as usize;
    if count == 1 {
        return vec![(0.0, container.min(span))];
    }
    (0..count)
        .map(|i| {
            (
                i as f64 * (span + gap),
                span.min(container - i as f64 * (span + gap)),
            )
        })
        .collect()
}

/// `svg_utils.px`: Python's `round`, half to even; never `-0`.
pub(crate) fn px(v: f64) -> f64 {
    v.round_ties_even() + 0.0
}

/// `translate_group`: no wrapper for a translate that does nothing.
fn translate_group(x: f64, y: f64, nodes: Vec<Node>) -> Vec<Node> {
    if x == 0.0 && y == 0.0 {
        return nodes;
    }
    vec![Node::group(Role::Chrome, Attrs::new(), nodes).translate(x, y)]
}

/// `_measure_offset`: the left offset of the measure block within its card. Alignment belongs to the
/// span the reader sees, not to a width the renderer chose.
fn measure_offset(align: TextAlign, available: f64, used: f64) -> f64 {
    let slack = (available - used).max(0.0);
    match align {
        TextAlign::Left => 0.0,
        TextAlign::Center => slack / 2.0,
        TextAlign::Right => slack,
    }
}

/// `_column_divider_line`: a vertical line between prose columns, its `x` snapped to the pixel.
fn column_divider(x: f64, height: f64, divider: &DividerStyle) -> Node {
    let attrs = Attrs::new()
        .num("x1", px(x))
        .num("y1", 0.0)
        .num("x2", px(x))
        .num("y2", height)
        .set("stroke", divider.color.as_str())
        .num("stroke-width", divider.width)
        .set_if(divider.dasharray.is_some(), "stroke-dasharray", divider.dasharray.as_ref().map_or("", |d| d.as_str()));
    Node::element(Element { tag: "line".into(), attrs, children: Vec::new() })
}

/// `_ProseFlow`: how one text block is measured and flowed, with the columns it settled on.
struct Flow {
    boxes: Vec<(f64, f64)>,
    measure: f64,
    table_box: (f64, f64),
    table_measure: Option<f64>,
    metrics: Vec<BlockMetrics>,
    stretches: Vec<Stretch>,
    n_cols: usize,
    /// The width of a heading: the columns it titles, or the one measure.
    spanning_measure: f64,
}

impl Flow {
    /// `paints_whole`: one column at one width draws in one piece, without ever flowing.
    fn paints_whole(&self) -> bool {
        self.n_cols <= 1 && self.table_measure.is_none_or(|t| t == self.measure)
    }
}

/// `_prose_flow`: the columns `slot` earns the text, then where a lone column widens past its span.
fn flow(text: &Text, slot: Slot) -> Result<Flow, Diagnostic> {
    let (width, pad, plan) = (slot.width, text.pad, slot.plan);
    let Columns { mut boxes, mut measure, mut table_box, mut table_measure, mut metrics, mut stretches } = columns_of(text, width, plan.grid)?;
    let mut wanted = measure;
    if !text.blocks.is_empty() && text.blocks.iter().all(|b| matches!(b, AnyBlock::HorizontalRule(_))) {
        // A rule has no text to keep readable, so it crosses the whole card, whose edges are span edges
        // too; only an authored max_chars caps it.
        wanted = if text.max_chars.is_some() { text.measure(width) } else { width };
    } else if !plan.snap && stretches.iter().all(|s| s.n_cols() == 1) {
        // Columns are for text long enough to earn them. On a board where no block goes multi-column, a
        // lone column wraps at least as wide as sizing asked for, so a line that fits stays one line;
        // once it outgrows its span it sits in the whole card. Where some block does, the lone ones keep
        // to their span so every edge on the board is a span edge.
        wanted = text.renderer.max_content_width(&text.blocks).map_err(mdsvg_error)?.min(text.measure(width));
        if boxes.len() == 1 {
            // A card's lone span is a snapped-board measure; here the text aligns in the whole card the
            // author sees.
            boxes = vec![(0.0, width + 2.0 * pad)];
            table_box = boxes[0];
        }
    }
    if wanted > measure {
        measure = wanted;
        boxes = vec![(0.0, width + 2.0 * pad)];
        table_box = boxes[0];
        table_measure = table_measure.map(|t| t.max(measure));
        // Without a table of another width the block paints in one piece and never reads its metrics.
        if table_measure.is_some_and(|t| t != measure) {
            (metrics, stretches) = measured(text, measure, table_measure, 1)?;
        }
    }
    // A heading wraps at the columns it titles, so it is measured there too.
    let n_cols = stretches.iter().map(Stretch::n_cols).max().unwrap_or(1);
    let mut spanning_measure = measure;
    if n_cols > 1 {
        let (last_x, last_width) = boxes[n_cols - 1];
        spanning_measure = last_x + last_width - boxes[0].0 - 2.0 * pad;
        let at_span = text.metrics(&text.blocks, spanning_measure)?;
        for stretch in stretches.iter().filter(|s| s.spanning) {
            for i in stretch.first..stretch.stop {
                if matches!(text.blocks[i], AnyBlock::Heading(_)) {
                    metrics[i] = at_span[i].clone();
                }
            }
        }
    }
    Ok(Flow { boxes, measure, table_box, table_measure, metrics, stretches, n_cols, spanning_measure })
}

/// `render_block_window`'s elements as nodes; the `<style>` is taken off the first window rendered,
/// mdsvg's public surface being the contract.
struct Windows<'a> {
    renderer: &'a SVGRenderer,
    style: Option<Element>,
}

impl Windows<'_> {
    fn of(&mut self, block: &AnyBlock, width: f64, start: usize, count: Option<usize>) -> Result<Vec<Node>, Diagnostic> {
        let rendered = self.renderer.render_block_window(block, width, start, count).map_err(mdsvg_error)?;
        let _ = self.style.get_or_insert(rendered.style);
        Ok(mdsvg::indented_nodes(&rendered.elements))
    }
}

/// A flowing stretch's columns, and the height of the tallest.
fn flowed(text: &Text, flow: &Flow, stretch: &Stretch, insets: &[f64], plan: Plan, divider: &DividerStyle, windows: &mut Windows) -> Result<(Vec<Node>, f64), Diagnostic> {
    let mut columns: Vec<Vec<(usize, usize, usize)>> = vec![Vec::new(); stretch.n_cols()];
    for (unit, &column) in stretch.units.iter().zip(&stretch.assignment) {
        let current = &mut columns[column];
        match current.last_mut() {
            Some(last) if last.0 == unit.block_id => last.2 = unit.line_index,
            _ => current.push((unit.block_id, unit.line_index, unit.line_index)),
        }
    }
    let mut parts = Vec::new();
    let mut tallest = f64::NEG_INFINITY;
    for (ci, column) in columns.iter().enumerate() {
        let mut y = 0.0;
        let mut elements = Vec::new();
        for (position, &(block_index, first, last)) in column.iter().enumerate() {
            let m = &flow.metrics[block_index];
            // A block opening a column sits at the top: both the gap that would separate it from
            // preceding text and the whitespace it draws above itself collapse, and a heading rises to
            // the first baseline the other columns open on.
            let opens_column = position == 0;
            if !opens_column {
                y += m.space_before;
            }
            let lift = if opens_column { m.column_lift } else { 0.0 };
            let window = windows.of(&text.blocks[block_index], flow.measure, first, Some(last - first + 1))?;
            elements.extend(translate_group(0.0, px(y - lift), window));
            y += py_fsum(&m.line_advances[first..=last]) + m.item_gap * (last - first) as f64 - lift;
        }
        tallest = tallest.max(y);
        parts.extend(translate_group(px(insets[ci]), 0.0, elements));
    }
    if divider.visible {
        for &(x, box_width) in &flow.boxes[..stretch.n_cols() - 1] {
            // Midway through the card gap between this span's box and the next; boxes are
            // container-relative and the slot starts `pad` inside it.
            parts.push(column_divider(x + box_width + plan.grid.gap / 2.0 - text.pad, tallest, divider));
        }
    }
    Ok((parts, tallest))
}

/// `render_prose_svg`: the text flowed across the spans its plan gives it, aligned in each.
pub fn text(style: &Style, source: &str, book: &FontBook, slot: Slot) -> Result<TextBlock, Diagnostic> {
    let text = text_setup(style, source, book)?;
    let flow = flow(&text, slot)?;
    let (pad, align) = (text.pad, style.text.align);
    let insets: Vec<f64> = flow.boxes.iter().map(|&(x, width)| x + measure_offset(align, width - 2.0 * pad, flow.measure)).collect();
    if flow.paints_whole() {
        let rendered = text.renderer.render_content(&text.blocks, flow.measure, 0.0).map_err(mdsvg_error)?;
        let content = [
            mdsvg::indented_nodes(std::slice::from_ref(&rendered.style)),
            vec![Node::chars(Chars::new("\n", Escape::Attr))],
            mdsvg::indented_nodes(&rendered.elements),
        ]
        .concat();
        return Ok(TextBlock { nodes: translate_group(px(insets[0]), 0.0, content), height: rendered.height });
    }
    let mut windows = Windows { renderer: &text.renderer, style: None };
    let table_x = flow.table_measure.map_or(0.0, |measure| flow.table_box.0 + measure_offset(align, flow.table_box.1 - 2.0 * pad, measure));
    let heading_x = flow.boxes[0].0 + measure_offset(align, (flow.boxes[0].1 - 2.0 * pad).max(flow.spanning_measure), flow.spanning_measure);
    let mut y = 0.0;
    let mut painted = Vec::new();
    for stretch in &flow.stretches {
        if !stretch.spanning {
            y += flow.metrics[stretch.first].space_before;
            let (columns, height) = flowed(&text, &flow, stretch, &insets, slot.plan, &style.text.column.divider, &mut windows)?;
            painted.extend(translate_group(0.0, px(y), columns));
            y += height;
            continue;
        }
        for index in stretch.first..stretch.stop {
            let (block, m) = (&text.blocks[index], &flow.metrics[index]);
            y += m.space_before;
            let lift = if index == 0 { m.leading_margin } else { 0.0 };
            let (x, block_width) = match (block, flow.table_measure) {
                (AnyBlock::Table(_), Some(measure)) => (table_x, measure),
                (AnyBlock::Table(_), None) => return Err(diag::err_internal("a table spans only past the measure, so it has one")),
                _ => (heading_x, flow.spanning_measure),
            };
            let window = windows.of(block, block_width, 0, None)?;
            painted.extend(translate_group(px(x), px(y - lift), window));
            y += py_fsum(&m.line_advances) - lift;
        }
    }
    let style_block = windows.style.ok_or_else(|| diag::err_internal("a flowed text paints a window"))?;
    let nodes = [mdsvg::indented_nodes(std::slice::from_ref(&style_block)), painted].concat();
    Ok(TextBlock { nodes, height: y })
}

/// What stacking a board's title and text above its layout adds: `BoardContentBox`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ContentBox {
    /// The inset from the block's top edge to the first element.
    pub content_top: f64,
    pub non_layout_height: f64,
    pub gap_before_layout: f64,
}

/// `compute_board_content_box` for a title or the band that holds it with its controls
/// (`inline_band_height`, in place of both the title's and the variables'), a text and a variables
/// band: the title flows straight into the text (the heading's own bottom margin is the rhythm), any
/// other block sits one `gap` below whatever is above it, and so does the layout. `card_padding`
/// insets the first element, as a chart's ink is inset in its own card.
/// `opens_with_text` (a text row leads the layout): the layout starts `card_padding` above where a gap
/// would put it, so a title directly over it reads as its own heading flowing into the text.
pub fn content_box(
    title_height: f64,
    inline_band_height: f64,
    text_height: f64,
    variables_height: f64,
    gap: f64,
    has_layout_items: bool,
    opens_with_text: bool,
    card_padding: f64,
) -> ContentBox {
    let (mut height, mut has_prev, mut prev_is_title) = (0.0, false, false);
    if inline_band_height > 0.0 {
        (height, has_prev) = (inline_band_height, true);
    } else if title_height > 0.0 {
        (height, has_prev, prev_is_title) = (title_height, true, true);
    }
    if text_height > 0.0 {
        if has_prev && !prev_is_title {
            height += gap;
        }
        height += text_height;
        (has_prev, prev_is_title) = (true, false);
    }
    if variables_height > 0.0 {
        if has_prev {
            height += gap;
        }
        height += variables_height;
        (has_prev, prev_is_title) = (true, false);
    }
    let content_top = if has_prev { card_padding } else { 0.0 };
    ContentBox {
        content_top,
        non_layout_height: height + content_top,
        gap_before_layout: if prev_is_title && opens_with_text {
            -card_padding
        } else if has_layout_items && has_prev {
            gap
        } else {
            0.0
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_card_between_one_and_two_spans_offers_one_span() {
        let span = grid_span(1128.0, 3, 20.0);
        for fraction in [0.35, 0.4, 0.45] {
            assert_eq!(span_boxes(1128.0 * fraction, 1128.0, 3, 20.0), vec![(0.0, span)]);
        }
        assert_eq!(span_boxes(1128.0 * 0.25, 1128.0, 3, 20.0), vec![(0.0, 1128.0 * 0.25)]);
    }
}
