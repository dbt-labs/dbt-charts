//! AST node types for representing parsed Markdown.
//!
//! Transliterated from `libs/markdown-svg/src/mdsvg/types.py`. Same names,
//! same shape; `Document` is `Vec<AnyBlock>` where Python uses `List[AnyBlock]`.

use d3rs::invariant::broken;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SpanType {
    Text,
    Bold,
    Italic,
    BoldItalic,
    Code,
    Link,
    Image,
}

/// An inline text span with styling.
#[derive(Debug, Clone, PartialEq)]
pub struct Span {
    pub text: String,
    pub span_type: SpanType,
    pub url: Option<String>,
    pub title: Option<String>,
    /// Authored `{width=X height=Y}`: image spans only.
    pub width: Option<f64>,
    pub height: Option<f64>,
    /// Link spans only: the label is inline code (``[`file.yml`](path)``),
    /// drawn as a code chip that links.
    pub code: bool,
    /// Emphasis a span inherits rather than declares by its type: code inside
    /// emphasis, or every span of an italic blockquote.
    pub bold: bool,
    pub italic: bool,
}

impl Span {
    /// Mirrors `Span.__post_init__`: a LINK or IMAGE span without a url is a
    /// construction error, not a case to degrade silently.
    pub fn new(
        text: impl Into<String>,
        span_type: SpanType,
        url: Option<String>,
        title: Option<String>,
    ) -> Self {
        if matches!(span_type, SpanType::Link | SpanType::Image) && url.is_none() {
            broken("Link/Image spans must have a URL");
        }
        Span {
            text: text.into(),
            span_type,
            url,
            title,
            width: None,
            height: None,
            code: false,
            bold: false,
            italic: false,
        }
    }

    pub fn text(text: impl Into<String>) -> Self {
        Span::new(text, SpanType::Text, None, None)
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Paragraph {
    pub spans: Vec<Span>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Heading {
    pub level: u8,
    pub spans: Vec<Span>,
}

impl Heading {
    /// Mirrors `Heading.__post_init__`.
    pub fn new(level: u8, spans: Vec<Span>) -> Self {
        if !(1..=6).contains(&level) {
            broken(&format!("Heading level must be 1-6, got {level}"));
        }
        Heading { level, spans }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct CodeBlock {
    pub code: String,
    pub language: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Blockquote {
    pub blocks: Vec<AnyBlock>,
}

/// A single item in a list.
///
/// `spans`: inline text spans for the item's own text.
/// `children`: nested block content (sub-lists, etc.) parsed from indented
/// body lines that follow the item's own text run.
#[derive(Debug, Clone, PartialEq)]
pub struct ListItem {
    pub spans: Vec<Span>,
    pub children: Vec<AnyBlock>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct UnorderedList {
    pub items: Vec<ListItem>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct OrderedList {
    pub items: Vec<ListItem>,
    pub start: Start,
}

/// The first number of an ordered list. Python counts in unbounded ints; the
/// parser keeps what fits a `u128` and names what does not, and the renderer
/// refuses a list it cannot number.
#[derive(Debug, Clone, PartialEq)]
pub enum Start {
    Number(u128),
    TooLarge(String),
}

#[derive(Debug, Clone, PartialEq)]
pub struct HorizontalRule;

#[derive(Debug, Clone, PartialEq)]
pub struct TableCell {
    pub spans: Vec<Span>,
    pub align: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct TableRow {
    pub cells: Vec<TableCell>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Table {
    pub header: TableRow,
    pub rows: Vec<TableRow>,
}

/// A standalone image block.
///
/// `url`: the image URL (may be relative or absolute).
/// `alt`: alt text for accessibility.
/// `title`: optional title attribute.
/// `width`/`height`: explicit pixel dims from markdown `{width=X height=Y}`.
#[derive(Debug, Clone, PartialEq)]
pub struct ImageBlock {
    pub url: String,
    pub alt: String,
    pub title: Option<String>,
    pub width: Option<f64>,
    pub height: Option<f64>,
}

/// A raw HTML block to be rendered via foreignObject when enabled.
#[derive(Debug, Clone, PartialEq)]
pub struct RawHtmlBlock {
    pub html: String,
}

#[derive(Debug, Clone, PartialEq)]
pub enum AnyBlock {
    Paragraph(Paragraph),
    Heading(Heading),
    CodeBlock(CodeBlock),
    Blockquote(Blockquote),
    UnorderedList(UnorderedList),
    OrderedList(OrderedList),
    HorizontalRule(HorizontalRule),
    Table(Table),
    ImageBlock(ImageBlock),
    RawHtmlBlock(RawHtmlBlock),
}

/// Whether any block, at any depth, is a raw HTML block. The `allow_raw_html=
/// True` arm of `renderer.py` (a `foreignObject` of nh3-sanitized markup) is
/// not ported, so a caller for which raw HTML is meant to render checks this
/// first and refuses by name.
pub fn contains_raw_html(blocks: &[AnyBlock]) -> bool {
    blocks.iter().any(|block| match block {
        AnyBlock::RawHtmlBlock(_) => true,
        AnyBlock::Blockquote(quote) => contains_raw_html(&quote.blocks),
        AnyBlock::UnorderedList(list) => list.items.iter().any(|i| contains_raw_html(&i.children)),
        AnyBlock::OrderedList(list) => list.items.iter().any(|i| contains_raw_html(&i.children)),
        AnyBlock::Paragraph(_)
        | AnyBlock::Heading(_)
        | AnyBlock::CodeBlock(_)
        | AnyBlock::HorizontalRule(_)
        | AnyBlock::Table(_)
        | AnyBlock::ImageBlock(_) => false,
    })
}

/// `markdown_blocks.image_urls`: every image destination, standalone or inline, in document order.
pub fn image_urls(blocks: &[AnyBlock]) -> Vec<&str> {
    fn run<'a>(spans: &'a [Span], out: &mut Vec<&'a str>) {
        out.extend(
            spans
                .iter()
                .filter(|s| s.span_type == SpanType::Image)
                .filter_map(|s| s.url.as_deref()),
        );
    }
    fn items<'a>(items: &'a [ListItem], out: &mut Vec<&'a str>) {
        for i in items {
            run(&i.spans, out);
            walk(&i.children, out);
        }
    }
    fn walk<'a>(blocks: &'a [AnyBlock], out: &mut Vec<&'a str>) {
        for block in blocks {
            match block {
                AnyBlock::ImageBlock(i) => out.push(&i.url),
                AnyBlock::Paragraph(p) => run(&p.spans, out),
                AnyBlock::Heading(h) => run(&h.spans, out),
                AnyBlock::Blockquote(q) => walk(&q.blocks, out),
                AnyBlock::UnorderedList(l) => items(&l.items, out),
                AnyBlock::OrderedList(l) => items(&l.items, out),
                AnyBlock::Table(t) => {
                    for row in std::iter::once(&t.header).chain(&t.rows) {
                        for cell in &row.cells {
                            run(&cell.spans, out);
                        }
                    }
                }
                AnyBlock::CodeBlock(_)
                | AnyBlock::HorizontalRule(_)
                | AnyBlock::RawHtmlBlock(_) => {}
            }
        }
    }
    let mut out = Vec::new();
    walk(blocks, &mut out);
    out
}

/// Type alias for the AST: a list of blocks.
pub type Document = Vec<AnyBlock>;
