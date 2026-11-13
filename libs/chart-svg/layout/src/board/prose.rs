//! A board's own title and text, measured and drawn through `mdsvg` with the theme's markdown style
//! (`markdown_style.rs`): `title_renderer`/`title_blocks`/`render_title` and `render_prose_svg` for
//! one column of prose, with `plan_board_prose`'s grid and measure laws (`prose.py::_columns_of`,
//! `_measure`) and `column_packer.py` (`choose_grid`, `grid_span`, `span_boxes`). The old composer's
//! copies of these laws are `chart-renderer/src/prose.rs` and `title.rs`; the cutover deletes them.
//!
//! What this stage does not model is refused by name: template syntax (the engine fills a board's
//! words first), images, emphasis in a family with no italic file to measure it against, raw HTML, a
//! letter case, and a text long enough to flow into more than one column.
//!
//! [`title`] is the root's one heading through `mdsvg`, filled with the theme's title ink, with its
//! measured height, the text's line box, its first baseline and its one-line width. [`text`] is one
//! column at the measure `plan_board_prose` and `_columns_of` choose, `choose_grid` and `span_boxes`
//! ported, aligned in its span. [`content_box`] is `compute_board_content_box`, with the band's height,
//! the inline band's in place of the title's and the controls'.

use board_model::style::board::TextAlign;
use board_model::style::font::LetterCase;
use board_model::style::style::Style;
use diagnostic::{diag, refuse, Diagnostic};
use mdsvg::renderer::SVGRenderer;
use measure::shaped::FontBook;
use mdsvg::style::Style as MdsvgStyle;
use mdsvg::types::{AnyBlock, Document, Heading, Span, SpanType};
use regex::Regex;
use scene::{Attrs, Chars, Child, Element, Escape, Node, Role};
use std::sync::LazyLock;

use crate::board::width::IntrinsicWidth;

use crate::markdown_style::{
    char_ratio, compact_style, font_slots, has_italic, readable_measure, refuse_case, title_overrides,
    Overrides,
};

/// `column_packer.MIN_CHARS`: a span narrower than this is not a readable column.
const MIN_CHARS: f64 = 45.0;
/// `column_packer.MIN_LINES_PER_COLUMN`: a column must earn this many lines to exist.
const MIN_LINES_PER_COLUMN: f64 = 5.0;

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

/// Where a board's text sits: the width it wraps in and the board it divides into columns.
#[derive(Debug, Clone, Copy)]
pub struct Slot {
    /// `prose_width`: the content width less the card padding on both sides.
    pub width: f64,
    /// `plan.board_width`: the root's content width.
    pub board_width: f64,
    /// `card_row_gap`: the gap the board's cards are pitched by.
    pub gap: f64,
}

/// Whether `text` writes Jinja, which layout reads only once it is filled.
pub fn has_template_syntax(text: &str) -> bool {
    ["{{", "{%", "{#"].iter().any(|syntax| text.contains(syntax))
}

fn refuse_template(text: &str, what: &str) -> Result<(), Diagnostic> {
    if has_template_syntax(text) {
        return Err(Diagnostic::refused(refuse!(
            "a board's {what} with template syntax is not supported"
        )));
    }
    Ok(())
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
    SVGRenderer::new(md_style, slots).map_err(diag::err_internal)
}

/// The title as the heading production draws it (`board_title_markdown`: `# title`), with the
/// document it is the only block of.
fn title_blocks(title: &str, italic: bool) -> Result<(Document, Heading), Diagnostic> {
    let blocks = mdsvg::parse(&format!("# {title}"));
    let [AnyBlock::Heading(heading)] = blocks.as_slice() else {
        return Err(Diagnostic::refused(refuse!(
            "a board's title that is not one line of text is not supported"
        )));
    };
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
        let floor = renderer.min_content_width(blocks).map_err(diag::err_internal)?;
        let one_line = renderer.max_content_width(blocks).map_err(diag::err_internal)?;
        Ok(IntrinsicWidth { min: Some(floor), preferred: Some(one_line.min(measure)) }.inset(inset))
    };
    let mut width = IntrinsicWidth::NONE;
    if let Some(title) = title {
        let (blocks, _, renderer) = title_setup(style, title, book, kind)?;
        width = width.combine_max(ask(&renderer, &blocks)?);
    }
    if let Some(text) = text {
        let (blocks, _, renderer) = text_setup(style, text, book)?;
        width = width.combine_max(ask(&renderer, &blocks)?);
    }
    Ok(width)
}

/// What sizing, planning and painting a board's title all read it through (`title_blocks` and
/// `title_renderer`): its heading, and the renderer of the root title's typography.
fn title_setup(style: &Style, title: &str, book: &FontBook, kind: TitleKind) -> Result<(Document, Heading, SVGRenderer), Diagnostic> {
    refuse_template(title, "title")?;
    refuse_case(&style.title.font.case, "board title")?;
    let overrides = title_overrides(style, kind.level, kind.root)?;
    let italic = has_italic(&compact_style(style, &overrides)?);
    let (blocks, heading) = title_blocks(title, italic)?;
    let renderer = renderer(book, style, &overrides)?;
    Ok((blocks, heading, renderer))
}

/// What sizing, planning and painting a board's text all read it through (`prose_blocks` and the
/// body renderer): the blocks with their small headings bolded, the body's markdown style and its
/// renderer.
fn text_setup(style: &Style, source: &str, book: &FontBook) -> Result<(Document, MdsvgStyle, SVGRenderer), Diagnostic> {
    refuse_template(source, "text")?;
    refuse_case(&style.text.font.case, "board text")?;
    let md_style = compact_style(style, &Overrides::PROSE)?;
    let mut blocks = mdsvg::parse(source);
    refuse_blocks(&blocks, "text", has_italic(&md_style))?;
    if blocks.iter().any(|b| matches!(b, AnyBlock::Heading(_))) && style.title.font.case != LetterCase::None {
        return Err(Diagnostic::refused(refuse!("a board's text with a heading under a title letter case is not supported")));
    }
    bold_small_headings(&mut blocks, |level| md_style.get_heading_size(level), md_style.base_font_size);
    let renderer = renderer(book, style, &Overrides::PROSE)?;
    Ok((blocks, md_style, renderer))
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
        .map_err(diag::err_internal)?;
    let line_box = renderer
        .heading_text_box(&heading, width)
        .map_err(diag::err_internal)?;
    let baseline = renderer
        .first_baseline(&blocks, width)
        .map_err(diag::err_internal)?;
    let one_line_width = renderer
        .max_content_width(&blocks)
        .map_err(diag::err_internal)?;
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

/// `style_prose_headings`: a heading drawn at body size takes the bold weight.
fn bold_small_headings(blocks: &mut Document, sizes: impl Fn(u8) -> f64, body: f64) {
    for block in blocks {
        if let AnyBlock::Heading(heading) = block {
            if sizes(heading.level) <= body {
                for span in &mut heading.spans {
                    if span.span_type == SpanType::Text {
                        span.span_type = SpanType::Bold;
                    }
                }
            }
        }
    }
}

/// `choose_grid`: 3 (thirds), 2 (halves) or 1 whole-board span.
fn choose_grid(board_width: f64, gap: f64, pad: f64, char_px: f64) -> usize {
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

fn grid_span(board_width: f64, grid: usize, gap: f64) -> f64 {
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
fn span_boxes(container: f64, board_width: f64, grid: usize, gap: f64) -> Vec<(f64, f64)> {
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

/// `render_prose_svg` for one column: the text laid out at its measure, aligned in its span.
pub fn text(
    style: &Style,
    source: &str,
    book: &FontBook,
    slot: Slot,
) -> Result<TextBlock, Diagnostic> {
    let (blocks, md_style, renderer) = text_setup(style, source, book)?;
    let body = md_style.base_font_size;
    let char_px = char_ratio(book, &md_style)? * body;
    let (pad, max_chars) = (style.frame.card_padding, style.text.column.max_chars);
    let boxes = span_boxes(
        slot.width + 2.0 * pad,
        slot.board_width,
        choose_grid(slot.board_width, slot.gap, pad, char_px),
        slot.gap,
    );
    let narrowest = boxes.iter().map(|(_, w)| *w).fold(f64::INFINITY, f64::min) - 2.0 * pad;
    let readable = readable_measure(char_px, max_chars);
    let mut measure = narrowest.min(readable);
    let mut first = boxes[0];
    let ceiling = style
        .text
        .column
        .max_number
        .map_or(boxes.len(), |n| boxes.len().min(n as usize));
    let mut rendered = renderer
        .render_content(&blocks, measure, 0.0)
        .map_err(diag::err_internal)?;
    // `_columns_of` decides the columns at the span measure, before the lone column is widened.
    // Production packs the text with a penalty packer and earns a column per `MIN_LINES_PER_COLUMN`
    // lines; the rendered height here stands in for its total, one line short of two columns' worth,
    // so a text that could earn a second column is refused rather than drawn as one.
    let line = body * md_style.line_height;
    if ceiling > 1 && rendered.height / line >= 2.0 * MIN_LINES_PER_COLUMN - 1.0 {
        return Err(Diagnostic::refused(refuse!(
            "a board's text long enough to flow into columns is not supported"
        )));
    }
    let wanted = if !blocks.is_empty() && blocks.iter().all(|b| matches!(b, AnyBlock::HorizontalRule(_))) {
        // A rule has no text to keep readable, so it crosses the whole card; only an authored
        // max_chars caps it.
        max_chars.map_or(slot.width, |_| slot.width.min(readable))
    } else {
        // A lone column wraps at least as wide as sizing asked for, so a line that fits stays one line.
        renderer
            .max_content_width(&blocks)
            .map_err(diag::err_internal)?
            .min(slot.width.min(readable))
    };
    // No board this stage draws has multi-column text, so a card's lone span is
    // never a snapped measure: the text aligns in the whole card.
    if boxes.len() == 1 {
        first = (0.0, slot.width + 2.0 * pad);
    }
    if wanted > measure {
        measure = wanted;
        first = (0.0, slot.width + 2.0 * pad);
        rendered = renderer
            .render_content(&blocks, measure, 0.0)
            .map_err(diag::err_internal)?;
    }
    let slack = (first.1 - 2.0 * pad - measure).max(0.0);
    let inset = first.0
        + match style.text.align {
            TextAlign::Left => 0.0,
            TextAlign::Center => slack / 2.0,
            TextAlign::Right => slack,
        };
    let content = [
        mdsvg::indented_nodes(std::slice::from_ref(&rendered.style)),
        vec![Node::chars(Chars::new("\n", Escape::Attr))],
        mdsvg::indented_nodes(&rendered.elements),
    ]
    .concat();
    let nodes = if inset == 0.0 {
        content
    } else {
        vec![Node::group(Role::Chrome, Attrs::new(), content)
            .translate(inset.round_ties_even(), 0.0)]
    };
    Ok(TextBlock {
        nodes,
        height: rendered.height,
    })
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
