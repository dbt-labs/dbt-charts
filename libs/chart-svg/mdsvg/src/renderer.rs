//! Layout of a parsed Markdown AST into `scene` nodes: every painted thing is an
//! `Element` of raw attribute values and text, and `print` is the one place
//! that escapes and serializes. `render_content` is the single layout pass;
//! the extent it reserves is `RenderResult::height`.
//!
//! Layout transliterated from `libs/markdown-svg/src/mdsvg/renderer.py`. Ports
//! headings, paragraphs, fenced code blocks (plain and highlighted),
//! blockquotes, ordered/unordered lists, tables, and horizontal rules —
//! every block kind the ladder's snippets reach: `RenderResult`,
//! `RenderContext`, `TableRowLayout`, `_apply_case`, `_round_half`,
//! `_numeric_weight`, `_wrap_colored_line`, `_truncate_segments`,
//! `SVGRenderer`'s constructor/measurement/text-block machinery and every
//! `_render_*` block arm above (including `_render_table`/`_render_
//! horizontal_rule` and their own `_table_cell_runs`/`_table_cell_lines_
//! to_inner_svg`/`_measure_table_row_layout`/
//! `_render_table_row` helpers; `run_sequence_width` is Rust-only), and
//! `TextRun`. `used_faces` is renamed `used_fonts` (guarded token).
//!
//! `measure_blocks`/`render_block_window` are also ported — the public
//! contract a column packer needs (mdsvg owns line breaking; a caller
//! decides where the breaks go); a table/hr counts as ONE unsplittable
//! block there, same as a heading or a code block, so porting `render_
//! block`'s own table/hr arms is what the packer needed, not a separate
//! measure-side port. Pygments-driven code highlighting (`highlight.py`,
//! gated on `style.code_highlight`) is ported for YAML only: a fence naming
//! any other language Pygments resolves refuses by name while it is on, and
//! an alias Pygments does not know takes the plain branch. A raw HTML block
//! paints as the escaped text of its source (`_render_raw_html_block` with
//! `allow_raw_html=False`, the constructor default and the only value the
//! `none` and `safe-subset` tiers reach); the `foreignObject` arm behind
//! `allow_raw_html=True`, with its nh3 sanitization, is not ported, and a
//! caller that would set it checks `types::contains_raw_html` and refuses
//! first, so that arm's `css_font_family_stack`-wrapped `font-family`
//! (`renderer.py:1696`) has no Rust counterpart to wrap. Block and inline
//! images paint at their authored, `data:`-decoded (`images.rs`), or
//! fallback size; a file or remote read is not ported and refuses by name,
//! as do an inline image in a table cell and a `measure_blocks` paragraph
//! whose lines an image makes unequal in height.

use std::cell::{Cell, RefCell};
use std::collections::{HashMap, HashSet};

use crate::css::css_font_family_stack;
use crate::highlight::{highlight_code, CodeSegment};
use crate::images::{data_uri_image_size, is_data_uri, ImageSize};
use crate::parser::parse as parse_markdown;
use crate::style::Style;
use crate::types::{
    AnyBlock, Blockquote, CodeBlock, Document, Heading, ImageBlock, ListItem, OrderedList,
    Paragraph, RawHtmlBlock, Span, SpanType, Table, TableCell, TableRow, UnorderedList,
};
use crate::utils::{collapse_spaces, format_number2 as fmt};
use d3rs::py_float::{py_float_sum, py_sum, PyNum};
use d3rs::py_str::is_py_space;
use measure::fonts::{
    split_token_precise, wrap_measured_pieces, FontMeasurer, FontSlot, FontSlots, WrapPiece,
};
use scene::{Attrs, Chars, Child, Element, Escape, Extent, Node};
use sha2::{Digest, Sha256};

/// One markdown element. Attribute values and text stay raw: `print` escapes them.
fn el(tag: &str, attrs: Vec<(&'static str, String)>, children: Vec<Child>) -> Element {
    Element {
        tag: tag.to_string(),
        attrs: attrs
            .into_iter()
            .fold(Attrs::new(), |attrs, (name, value)| attrs.set(name, value)),
        children,
    }
}

/// Authored text: markdown's own, printed as mdsvg's markup prints it (quotes and whitespace
/// as character references).
fn quoted(text: impl Into<String>) -> Child {
    Child::Text(Chars::new(text, Escape::Mdsvg))
}

/// Whitespace between elements, printed as written.
fn ws(text: &str) -> Child {
    Child::Text(Chars::new(text, Escape::Attr))
}

/// A `<text>`-like element holding `runs`. Empty runs still print `<text ...></text>`, not a
/// self-closing tag.
fn text_el(attrs: Vec<(&'static str, String)>, mut runs: Vec<Child>) -> Element {
    if runs.is_empty() {
        runs.push(quoted(""));
    }
    el("text", attrs, runs)
}

fn table_gridline(
    x1: f64,
    y1: f64,
    x2: f64,
    y2: f64,
    stroke: &[(&'static str, String)],
) -> Element {
    let mut attrs = vec![
        ("x1", fmt(x1)),
        ("y1", fmt(y1)),
        ("x2", fmt(x2)),
        ("y2", fmt(y2)),
    ];
    attrs.extend(stroke.iter().cloned());
    el("line", attrs, vec![])
}

/// `elements` each on its own line, indented two spaces: how markdown markup has always laid out.
fn lines<T>(elements: &[Element], whitespace: fn(String) -> T, element: fn(Element) -> T) -> Vec<T> {
    let mut out = Vec::with_capacity(elements.len() * 2);
    for (i, e) in elements.iter().enumerate() {
        out.push(whitespace(if i == 0 { "  " } else { "\n  " }.to_string()));
        out.push(element(e.clone()));
    }
    out
}

/// [`lines`] as children of an enclosing element.
pub fn indented(elements: &[Element]) -> Vec<Child> {
    lines(elements, |s| ws(&s), Child::Element)
}

/// [`lines`] at the top level of a scene.
pub fn indented_nodes(elements: &[Element]) -> Vec<Node> {
    lines(elements, |s| Node::Chars(Chars::new(s, Escape::Attr)), Node::Element)
}

/// First 8 hex chars of the SHA-256 digest of `input`. Mirrors Python's
/// `hashlib.sha256(input.encode(), usedforsecurity=False).hexdigest()[:8]`.
fn sha256_hex8(input: &str) -> String {
    Sha256::digest(input.as_bytes())
        .iter()
        .take(4)
        .map(|b| format!("{b:02x}"))
        .collect()
}

// Fixed order: also the order the rules are hashed and rendered in, so
// changing it would churn every class prefix for no visual reason.
const STYLE_RULE_NAMES: [&str; 6] = ["text", "mono", "heading", "code", "link", "blockquote"];

/// CSS declarations for each `.md-*` rule, keyed by its bare name, in
/// `STYLE_RULE_NAMES` order. Shared by `get_style_block` (which renders
/// them) and `compute_class_prefix` (which hashes them), so the two can
/// never drift out of sync. Mirrors `_style_rule_bodies`.
fn style_rule_bodies(style: &Style) -> HashMap<&'static str, String> {
    let text_weight = match &style.font_weight {
        Some(w) => format!(" font-weight: {};", w),
        None => String::new(),
    };
    let code_family = if style.code_font_family.is_empty() {
        &style.mono_font_family
    } else {
        &style.code_font_family
    };
    let mut code_extra = String::new();
    if !style.code_font_weight.is_empty() {
        code_extra += &format!(" font-weight: {};", style.code_font_weight);
    }
    if !style.code_font_style.is_empty() {
        code_extra += &format!(" font-style: {};", style.code_font_style);
    }
    if style.code_font_size != 0.0 {
        code_extra += &format!(" font-size: {}px;", fmt(style.code_font_size));
    }
    if !style.code_font_decoration.is_empty() {
        code_extra += &format!(" text-decoration: {};", style.code_font_decoration);
    }

    let heading_family = if style.heading_font_family.is_empty() {
        &style.font_family
    } else {
        &style.heading_font_family
    };

    let bq_family = if style.blockquote_font_family.is_empty() {
        &style.font_family
    } else {
        &style.blockquote_font_family
    };
    let mut bq_extra = format!(" font-family: {};", css_font_family_stack(bq_family));
    if !style.blockquote_font_weight.is_empty() {
        bq_extra += &format!(" font-weight: {};", style.blockquote_font_weight);
    }
    if !style.blockquote_font_style.is_empty() {
        bq_extra += &format!(" font-style: {};", style.blockquote_font_style);
    }
    if style.blockquote_font_size != 0.0 {
        bq_extra += &format!(" font-size: {}px;", fmt(style.blockquote_font_size));
    }
    if !style.blockquote_font_decoration.is_empty() {
        bq_extra += &format!(" text-decoration: {};", style.blockquote_font_decoration);
    }

    HashMap::from([
        (
            "text",
            format!(
                "font-family: {}; fill: {};{}",
                css_font_family_stack(&style.font_family),
                style.text_color,
                text_weight
            ),
        ),
        (
            "mono",
            format!(
                "font-family: {};",
                css_font_family_stack(&style.mono_font_family)
            ),
        ),
        (
            "heading",
            format!(
                "font-family: {}; fill: {}; font-weight: {};",
                css_font_family_stack(heading_family),
                style.get_heading_color(),
                style.heading_font_weight
            ),
        ),
        (
            "code",
            format!(
                "font-family: {}; fill: {};{code_extra}",
                css_font_family_stack(code_family),
                style.code_color
            ),
        ),
        ("link", format!("fill: {};", style.link_color)),
        (
            "blockquote",
            format!("fill: {};{}", style.blockquote_color, bq_extra),
        ),
    ])
}

/// Deterministic scope for a renderer's `.md-*` classes. Mirrors
/// `_compute_class_prefix`: inline SVG has no style scope in an HTML page,
/// so two boards sharing a page would have the *last* `.md-heading` rule
/// win for both without this. Hashing the rule bodies means identical
/// styles correctly share a scope while distinct styles never collide. The
/// hash input is the rules as printed (escaped), as Python hashes them.
fn compute_class_prefix(style: &Style) -> String {
    let bodies = style_rule_bodies(style);
    let css = STYLE_RULE_NAMES
        .iter()
        .map(|name| print::xml_escape_attr(&bodies[name]))
        .collect::<Vec<_>>()
        .join("\u{0}");
    sha256_hex8(&css)
}

// Vertical chip geometry constants — see Python's docstring for the
// measured-font derivation.
const CHIP_CAP_HEIGHT_RATIO: f64 = 0.72;
const CHIP_DESCENDER_RATIO: f64 = 0.20;

fn apply_case(text: &str, case: &str) -> String {
    match case {
        "upper" => text.to_uppercase(),
        "lower" => text.to_lowercase(),
        _ => text.to_string(),
    }
}

fn round_half(size: f64) -> f64 {
    // Python's `round()` is round-half-to-even (banker's rounding), unlike
    // `f64::round`'s round-half-away-from-zero — `round_ties_even` is the
    // one that agrees with it on the .25/.75 ties this snaps to.
    (size * 2.0).round_ties_even() / 2.0
}

fn numeric_weight(value: Option<&str>) -> Option<f64> {
    let value = value?;
    match value.trim_matches(is_py_space).to_lowercase().as_str() {
        "normal" => Some(400.0),
        "bold" => Some(700.0),
        // `float()` strips the Unicode `White_Space` set, not `\x1c`-`\x1f`.
        _ => value.trim().parse::<f64>().ok(),
    }
}

/// Wrap one code line's `(text, color)` segments into visual lines of at most
/// `max_chars` characters, cutting at fixed character intervals. A token is
/// split only when a cut boundary happens to land inside it.
fn wrap_colored_line(segments: &[CodeSegment], max_chars: i64) -> Vec<Vec<CodeSegment>> {
    let mut visual_lines: Vec<Vec<CodeSegment>> = Vec::new();
    let mut current: Vec<CodeSegment> = Vec::new();
    let mut current_len: i64 = 0;
    for (text, color) in segments {
        let mut remaining = text.as_str();
        while !remaining.is_empty() {
            let mut chunk_len = max_chars - current_len;
            if chunk_len <= 0 {
                visual_lines.push(std::mem::take(&mut current));
                current_len = 0;
                chunk_len = max_chars;
            }
            let chars: Vec<char> = remaining.chars().collect();
            let take = (chunk_len.max(0) as usize).min(chars.len());
            let chunk: String = chars[..take].iter().collect();
            remaining = &remaining[chunk.len()..];
            current_len += chunk.chars().count() as i64;
            current.push((chunk, color.clone()));
        }
    }
    visual_lines.push(current);
    visual_lines
}

/// Truncate a line's `(text, color)` segments to at most `max_len`
/// characters, dropping trailing segments/characters beyond the limit.
fn truncate_segments(segments: &[CodeSegment], max_len: i64) -> Vec<CodeSegment> {
    let mut out: Vec<CodeSegment> = Vec::new();
    let mut remaining = max_len;
    for (text, color) in segments {
        if remaining <= 0 {
            break;
        }
        let chars: Vec<char> = text.chars().collect();
        let take = (remaining.max(0) as usize).min(chars.len());
        let chunk: String = chars[..take].iter().collect();
        if !chunk.is_empty() {
            out.push((chunk, color.clone()));
        }
        remaining -= take as i64;
    }
    out
}

/// `renderer.py::TableRowLayout`: measured layout for one rendered table
/// row — `cell_lines[cell][line]` is that line's own text runs.
struct TableRowLayout {
    cell_lines: Vec<Vec<Vec<TextRun>>>,
    row_height: f64,
}

/// A render's nodes: the `<style>` its classes need and the painted elements in document order,
/// laid out for `height` at `width`.
#[derive(Debug, Clone)]
pub struct RenderResult {
    pub elements: Vec<Element>,
    pub style: Element,
    pub width: f64,
    pub height: f64,
    pub max_content_width: f64,
}

impl RenderResult {
    /// The `<style>` and the elements as children of an enclosing element, one per line.
    pub fn content(&self) -> Vec<Child> {
        let mut out = indented(std::slice::from_ref(&self.style));
        out.push(ws("\n"));
        out.extend(indented(&self.elements));
        out
    }

    /// The standalone `<svg>`; its extent is the size the layout reserved.
    pub fn svg(&self) -> Node {
        let width = fmt(self.width);
        let height = fmt(self.height);
        let mut lines = vec![self.style.clone()];
        lines.extend(self.elements.iter().cloned());
        let mut children = vec![Node::Chars(Chars::new("\n", Escape::Attr))];
        children.extend(indented_nodes(&lines));
        children.push(Node::Chars(Chars::new("\n", Escape::Attr)));
        Node::Svg {
            extent: Extent {
                width: self.width,
                height: self.height,
            },
            attrs: Attrs::new()
                .set("xmlns", "http://www.w3.org/2000/svg")
                .set("width", width.clone())
                .set("height", height.clone())
                .set("viewBox", format!("0 0 {width} {height}")),
            children,
        }
    }
}

/// Line-level measurement of one markdown block at a given width — enough
/// for a caller to decide where to break a document across boxes of
/// limited height (columns, pages, slides) without mdsvg knowing what a
/// box is. `line_count`/`line_advance` describe a block that may be split;
/// an unsplittable block reports one unit whose advance is its whole
/// height, so a packer can treat every block the same way
/// (`renderer.py::BlockMetrics`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct BlockMetrics {
    pub line_count: usize,
    pub line_advance: f64,
    pub space_before: f64,
    pub splittable: bool,
    pub keep_with_next: bool,
    /// Whitespace the block draws above its own content (headings) —
    /// reported separately so a caller placing this block at the top of a
    /// box can collapse it.
    pub leading_margin: f64,
}

#[derive(Debug, Clone)]
struct RenderContext {
    x: f64,
    y: f64,
    width: f64,
    indent: f64,
}

impl RenderContext {
    fn with_offset(&self, dy: f64) -> RenderContext {
        RenderContext {
            x: self.x,
            y: self.y + dy,
            width: self.width,
            indent: self.indent,
        }
    }

    fn with_indent(&self, additional_indent: f64) -> RenderContext {
        RenderContext {
            x: self.x + additional_indent,
            y: self.y,
            width: self.width - additional_indent,
            indent: self.indent + additional_indent,
        }
    }
}

/// `renderer.py::InlineImage`: an inline image's resolved on-page box.
#[derive(Debug, Clone)]
pub struct InlineImage {
    pub url: String,
    pub alt: String,
    pub width: f64,
    pub height: f64,
    /// Production holds a `data:` image's decoded width as an `int` until
    /// scaling makes it a float, and `sum()` adds an int without compensation.
    pub width_is_int: bool,
}

impl InlineImage {
    fn width_addend(&self) -> PyNum {
        if self.width_is_int {
            PyNum::Int(self.width)
        } else {
            PyNum::Float(self.width)
        }
    }
}

/// A run of text with consistent styling, or one inline image.
#[derive(Debug, Clone)]
pub struct TextRun {
    pub text: String,
    pub is_bold: bool,
    pub is_italic: bool,
    pub is_code: bool,
    pub is_link: bool,
    pub url: Option<String>,
    pub image: Option<InlineImage>,
}

/// `renderer.py::_Line`: one wrapped line, its baseline `ascent` below its
/// top and its `height`.
#[derive(Debug, Clone)]
struct Line {
    runs: Vec<TextRun>,
    ascent: f64,
    height: f64,
}

impl TextRun {
    fn plain(text: impl Into<String>) -> Self {
        TextRun {
            text: text.into(),
            is_bold: false,
            is_italic: false,
            is_code: false,
            is_link: false,
            url: None,
            image: None,
        }
    }

    /// An image run never merges with anything, another image included: each
    /// is its own `<image>`.
    pub fn same_style(&self, other: &TextRun) -> bool {
        self.image.is_none()
            && other.image.is_none()
            && self.is_bold == other.is_bold
            && self.is_italic == other.is_italic
            && self.is_code == other.is_code
            && self.is_link == other.is_link
            && self.url == other.url
    }

    pub fn with_text(&self, text: impl Into<String>) -> TextRun {
        TextRun {
            text: text.into(),
            ..self.clone()
        }
    }

    pub fn append(&self, text: &str) -> TextRun {
        self.with_text(format!("{}{}", self.text, text))
    }
}

pub struct SVGRenderer {
    pub style: Style,
    class_prefix: String,
    measurer: std::sync::Arc<FontMeasurer>,
    mono_char_width: Cell<Option<f64>>,
    mono_slot: Option<FontSlot>,
    bold_measurer: Option<std::sync::Arc<FontMeasurer>>,
    italic_measurer: Option<std::sync::Arc<FontMeasurer>>,
    bold_italic_measurer: Option<std::sync::Arc<FontMeasurer>>,
    heading_measurer: Option<std::sync::Arc<FontMeasurer>>,
    /// The regular font a weight can be re-instanced from; `None` for a
    /// renderer built from a bare font path (`from_font_path`).
    regular_slot: Option<FontSlot>,
    italic_slot: Option<FontSlot>,
    heading_slot: Option<FontSlot>,
    max_content_width: Cell<f64>,
    used_fonts: RefCell<HashSet<&'static str>>,
    /// Python's `fetch_image_sizes` (default false): whether an image block asks
    /// its source for an intrinsic size. Off, an image is sized by its authored
    /// `{width= height=}` or the style alone. On is not ported and refuses.
    fetch_image_sizes: bool,
}

impl SVGRenderer {
    /// Mirrors `SVGRenderer.__init__(style=..., fonts=...)` — the only
    /// construction shape production reaches (`font_path`/`mono_font_path`/
    /// system-default fallbacks are not ported; see the module doc).
    pub fn new(style: Style, fonts: FontSlots) -> Result<Self, String> {
        // `build_mdsvg_style` (`markdown_style.py:135-146`) reports the
        // `ValueError` of `Style.__post_init__` as `ERR-STYLE-CSS-VALUE-INVALID`
        // (`codes_compile.py:1956-1972`, message_template `Invalid style value
        // for markdown text: {detail}.`); every production `Style` is built
        // there, so a style that reaches a renderer here was refused there.
        style.validate_css().map_err(|detail| {
            format!("ERR-STYLE-CSS-VALUE-INVALID: Invalid style value for markdown text: {detail}.")
        })?;
        let measurer_for = |slot: &FontSlot| -> Result<std::sync::Arc<FontMeasurer>, String> {
            let m = measure::fonts::cached_measurer(&slot.path, slot.font_number, slot.weight);
            if !m.is_available() {
                return Err(format!(
                    "Precise text measurement is required, but font {:?} is unavailable",
                    slot.path
                ));
            }
            Ok(m)
        };

        let measurer = measurer_for(&fonts.regular)?;
        let bold_measurer = fonts.bold.as_ref().map(measurer_for).transpose()?;
        let italic_measurer = fonts.italic.as_ref().map(measurer_for).transpose()?;
        let bold_italic_measurer = fonts.bold_italic.as_ref().map(measurer_for).transpose()?;
        let heading_measurer = fonts.heading.as_ref().map(measurer_for).transpose()?;
        let class_prefix = compute_class_prefix(&style);

        Ok(SVGRenderer {
            style,
            class_prefix,
            measurer,
            mono_char_width: Cell::new(None),
            mono_slot: fonts.mono.clone(),
            bold_measurer,
            italic_measurer,
            bold_italic_measurer,
            heading_measurer,
            regular_slot: Some(fonts.regular.clone()),
            italic_slot: fonts.italic.clone(),
            heading_slot: fonts.heading.clone(),
            max_content_width: Cell::new(0.0),
            used_fonts: RefCell::new(HashSet::new()),
            fetch_image_sizes: false,
        })
    }

    /// The constructor's `font_path=`/`mono_font_path=` arm: measures against
    /// the file as it is, never re-weighting it to a requested `font-weight`
    /// (that needs the `fonts=` arm's fonts).
    pub fn from_font_path(
        style: Style,
        font_path: String,
        mono_font_path: String,
    ) -> Result<Self, String> {
        let fonts = FontSlots {
            regular: FontSlot::new(font_path),
            bold: None,
            italic: None,
            bold_italic: None,
            mono: Some(FontSlot::new(mono_font_path)),
            heading: None,
        };
        let mut renderer = Self::new(style, fonts)?;
        renderer.regular_slot = None;
        Ok(renderer)
    }

    /// Mirrors the constructor's `fetch_image_sizes=` keyword.
    pub fn with_fetch_image_sizes(mut self, fetch_image_sizes: bool) -> Self {
        self.fetch_image_sizes = fetch_image_sizes;
        self
    }

    /// A measurer for `font_face` re-weighted to `weight`, or `None` if not
    /// possible (no font slot, or the file has no weight axis to move).
    fn measurer_at_weight(
        &self,
        font_face: Option<&FontSlot>,
        weight: f64,
    ) -> Option<std::sync::Arc<FontMeasurer>> {
        let font_face = font_face?;
        let base = measure::fonts::cached_measurer(
            &font_face.path,
            font_face.font_number,
            font_face.weight,
        );
        if !base.supports_weight() {
            return None;
        }
        let measurer = measure::fonts::cached_measurer(
            &font_face.path,
            font_face.font_number,
            Some(weight as f32),
        );
        if measurer.is_available() {
            Some(measurer)
        } else {
            None
        }
    }

    /// Load precise monospace measurement on first actual code use.
    fn ensure_mono_char_width(&self) -> Result<f64, String> {
        if let Some(w) = self.mono_char_width.get() {
            return Ok(w);
        }
        let mono_slot = self
            .mono_slot
            .as_ref()
            .ok_or_else(|| "Precise monospace measurement is required, but no monospace FontMeasurer is available".to_string())?;
        let mono_measurer = measure::fonts::cached_measurer(
            &mono_slot.path,
            mono_slot.font_number,
            mono_slot.weight,
        );
        if !mono_measurer.is_available() {
            return Err(format!(
                "Precise text measurement is required, but font {:?} is unavailable",
                mono_slot.path
            ));
        }
        let width = mono_measurer.measure("M", 1.0);
        self.mono_char_width.set(Some(width));
        Ok(width)
    }

    /// Which of the supplied `FontSlots` this renderer has reached so far.
    /// Renamed from Python's `used_faces` (guarded token).
    pub fn used_fonts(&self) -> HashSet<&'static str> {
        self.used_fonts.borrow().clone()
    }

    /// Measure text width against the font that will actually be
    /// painted. See the Python docstring for the full fallback ladder;
    /// this is a line-for-line port of it.
    // Same eight-parameter signature as `renderer.py::_measure_text`.
    #[allow(clippy::too_many_arguments)]
    fn measure_text(
        &self,
        text: &str,
        font_size: f64,
        is_bold: bool,
        is_italic: bool,
        is_mono: bool,
        font_weight: Option<&str>,
        is_heading: bool,
    ) -> Result<f64, String> {
        if is_mono {
            let mono_size = self.inline_code_size(font_size);
            self.used_fonts.borrow_mut().insert("mono");
            return Ok(text.chars().count() as f64 * self.ensure_mono_char_width()? * mono_size);
        }

        let weight = numeric_weight(font_weight);

        // Only the plain run of a heading paints in the heading family; an
        // emphasized span inside it falls through to the body bold/italic
        // font, since `FontSlots` carries no heading-bold/heading-italic
        // combination to measure it against.
        if is_heading && !is_bold && !is_italic {
            if let Some(heading_slot) = &self.heading_slot {
                let measurer = weight
                    .and_then(|w| self.measurer_at_weight(Some(heading_slot), w))
                    .or_else(|| self.heading_measurer.clone());
                if let Some(measurer) = measurer {
                    self.used_fonts.borrow_mut().insert("heading");
                    return Ok(measurer.measure(text, font_size));
                }
            }
        }

        if let Some(weight) = weight {
            if !is_bold {
                let font_face = if is_italic {
                    self.italic_slot.as_ref()
                } else {
                    self.regular_slot.as_ref()
                };
                if let Some(weighted) = self.measurer_at_weight(font_face, weight) {
                    self.used_fonts.borrow_mut().insert(if is_italic {
                        "italic"
                    } else {
                        "regular"
                    });
                    return Ok(weighted.measure(text, font_size));
                }
            }
        }

        if is_bold && is_italic {
            let (measurer, label) = if let Some(m) = &self.bold_italic_measurer {
                (Some(m), "bold_italic")
            } else if let Some(m) = &self.italic_measurer {
                (Some(m), "italic")
            } else if let Some(m) = &self.bold_measurer {
                (Some(m), "bold")
            } else {
                (None, "")
            };
            if let Some(measurer) = measurer {
                self.used_fonts.borrow_mut().insert(label);
                return Ok(measurer.measure(text, font_size));
            }
            self.used_fonts.borrow_mut().insert("regular");
            let width = self.measurer.measure(text, font_size);
            let bold_ratio = self.style.bold_char_width_ratio / self.style.char_width_ratio;
            let italic_ratio = self.style.italic_char_width_ratio / self.style.char_width_ratio;
            return Ok(width * bold_ratio * italic_ratio);
        }

        if is_bold {
            if let Some(m) = &self.bold_measurer {
                self.used_fonts.borrow_mut().insert("bold");
                return Ok(m.measure(text, font_size));
            }
            self.used_fonts.borrow_mut().insert("regular");
            let width = self.measurer.measure(text, font_size);
            return Ok(width * (self.style.bold_char_width_ratio / self.style.char_width_ratio));
        }

        if is_italic {
            if let Some(m) = &self.italic_measurer {
                self.used_fonts.borrow_mut().insert("italic");
                return Ok(m.measure(text, font_size));
            }
            self.used_fonts.borrow_mut().insert("regular");
            let width = self.measurer.measure(text, font_size);
            return Ok(width * (self.style.italic_char_width_ratio / self.style.char_width_ratio));
        }

        self.used_fonts.borrow_mut().insert("regular");
        Ok(self.measurer.measure(text, font_size))
    }

    fn render_blocks_to_elements(
        &self,
        blocks: &Document,
        width: f64,
        padding: f64,
    ) -> Result<(Vec<Element>, f64), String> {
        self.max_content_width.set(0.0);
        let content_width = width - (padding * 2.0);

        let mut svg_elements: Vec<Element> = Vec::new();
        let mut current_y = padding;
        let mut prev_block: Option<&AnyBlock> = None;

        for block in blocks {
            if let Some(prev) = prev_block {
                current_y += self.inter_block_gap(prev, block);
            }
            let lift = if prev_block.is_none() {
                self.heading_margins(block).0
            } else {
                0.0
            };
            let ctx = RenderContext {
                x: padding,
                y: padding,
                width: content_width,
                indent: 0.0,
            };
            let (elements, height) =
                self.render_block(block, &ctx.with_offset(current_y - ctx.y - lift))?;
            svg_elements.extend(elements);
            current_y += height - lift;
            prev_block = Some(block);
        }

        let total_height = current_y + padding;
        Ok((svg_elements, total_height))
    }

    /// `renderer.py::max_content_width`: the narrowest width at which `blocks`
    /// lay out with no line wrapped (CSS max-content) — every paragraph,
    /// heading, list item and table cell on one line, plus the indents and
    /// padding the renderer puts around them. A block image asks for its set
    /// width or `style.image_width`; with neither it fills the container, so
    /// like rules and raw HTML it asks for none. Rounded up to 0.01px.
    pub fn max_content_width(&self, blocks: &[AnyBlock]) -> Result<f64, String> {
        Ok((self.blocks_max_content(blocks)? * 100.0).ceil() / 100.0)
    }

    fn blocks_max_content(&self, blocks: &[AnyBlock]) -> Result<f64, String> {
        blocks
            .iter()
            .try_fold(0.0_f64, |widest, block| Ok(widest.max(self.block_max_content(block)?)))
    }

    /// `renderer.py::_one_line_width`: width of `runs` on one line, the
    /// pieces wrapping would lay out. Runs are built for no available width,
    /// so an inline image keeps its natural size rather than shrinking to fit.
    fn one_line_width(
        &self,
        runs: &[TextRun],
        font_size: f64,
        font_weight: Option<&str>,
        is_heading: bool,
    ) -> Result<f64, String> {
        let pieces = self.run_pieces(runs, f64::INFINITY, font_size, font_weight, is_heading)?;
        let widths = pieces.iter().map(|piece| match piece.meta.and_then(|i| runs[i].image.as_ref()) {
            Some(image) => image.width_addend(),
            None => PyNum::Float(piece.width),
        });
        let separators: Vec<f64> = pieces.iter().skip(1).map(|p| p.separator_width).collect();
        Ok(py_sum(widths) + py_float_sum(&separators))
    }

    /// `renderer.py::_table_column_extents`: each column's narrowest and
    /// widest width, padding included, rounded up to 0.01px.
    fn table_column_extents(&self, table: &Table) -> Result<(Vec<f64>, Vec<f64>), String> {
        let padding = 2.0 * self.style.table_cell_padding;
        let size = self.style.base_font_size;
        let mut mins = vec![0.0_f64; table.header.cells.len()];
        let mut maxs = mins.clone();
        let rows = std::iter::once((&table.header, true))
            .chain(table.rows.iter().map(|row| (row, false)));
        for (row, is_header) in rows {
            for (col, cell) in row.cells.iter().enumerate() {
                let runs = self.table_cell_runs(cell, is_header)?;
                let mut widest_token = 0.0_f64;
                for run in &runs {
                    for token in split_whitespace_tokens(&run.text) {
                        if token.chars().all(is_py_space) {
                            continue;
                        }
                        widest_token = widest_token.max(self.measure_text(
                            token,
                            size,
                            run.is_bold,
                            run.is_italic,
                            run.is_code,
                            None,
                            false,
                        )?);
                    }
                }
                mins[col] = mins[col].max(widest_token + padding);
                maxs[col] = maxs[col].max(self.one_line_width(&runs, size, None, false)? + padding);
            }
        }
        let round_up = |w: &f64| (w * 100.0).ceil() / 100.0;
        Ok((mins.iter().map(round_up).collect(), maxs.iter().map(round_up).collect()))
    }

    /// `renderer.py::min_content_width` for a list of tables: the narrowest
    /// whole-pixel width at which no table splits a word. Any other block is
    /// refused by name.
    pub fn table_min_content_width(&self, tables: &[AnyBlock]) -> Result<f64, String> {
        let mut floor = 0.0_f64;
        for block in tables {
            let AnyBlock::Table(table) = block else {
                return Err("table_min_content_width: not a table".to_string());
            };
            floor = floor.max(py_float_sum(&self.table_column_extents(table)?.0));
        }
        Ok(floor.ceil())
    }

    /// `renderer.py::_table_column_widths`: content-sized columns, wrapping
    /// only when `available` forces it.
    fn table_column_widths(&self, table: &Table, available: f64) -> Result<Vec<f64>, String> {
        let (mins, maxs) = self.table_column_extents(table)?;
        if py_float_sum(&maxs) <= available {
            return Ok(maxs);
        }
        if py_float_sum(&mins) > available {
            return Ok(vec![available / mins.len() as f64; mins.len()]);
        }
        let flex: Vec<f64> = mins.iter().zip(&maxs).map(|(lo, hi)| hi - lo).collect();
        let slack = (available - py_float_sum(&mins)) / py_float_sum(&flex);
        Ok(mins.iter().zip(&flex).map(|(lo, extra)| lo + extra * slack).collect())
    }

    fn table_max_content(&self, table: &Table) -> Result<f64, String> {
        Ok(py_float_sum(&self.table_column_extents(table)?.1))
    }

    fn block_max_content(&self, block: &AnyBlock) -> Result<f64, String> {
        let style = &self.style;
        match block {
            AnyBlock::Paragraph(p) => self.one_line_width(
                &self.build_text_runs(&p.spans, 0.0)?,
                style.base_font_size,
                Some("normal"),
                false,
            ),
            AnyBlock::Heading(h) => self.one_line_width(
                &self.build_text_runs(&h.spans, 0.0)?,
                style.get_heading_size(h.level),
                Some(&style.heading_font_weight),
                true,
            ),
            AnyBlock::UnorderedList(l) => self.list_max_content(&l.items),
            AnyBlock::OrderedList(l) => self.list_max_content(&l.items),
            AnyBlock::Blockquote(bq) => {
                let mut widest = 0.0_f64;
                for inner in &bq.blocks {
                    let width = match inner {
                        AnyBlock::Paragraph(p) => {
                            let (spans, size) = self.blockquote_text(p);
                            self.one_line_width(
                                &self.build_text_runs(&spans, 0.0)?,
                                size,
                                Some("normal"),
                                false,
                            )?
                        }
                        other => self.block_max_content(other)?,
                    };
                    widest = widest.max(width);
                }
                Ok(style.blockquote_padding + widest)
            }
            AnyBlock::Table(t) => self.table_max_content(t),
            AnyBlock::CodeBlock(code) => {
                let char_width = self.block_code_size() * self.ensure_mono_char_width()?;
                // `str.splitlines()` boundaries; only the longest line's length matters.
                let longest = code
                    .code
                    .split(['\n', '\r', '\x0b', '\x0c', '\x1c', '\x1d', '\x1e', '\u{85}', '\u{2028}', '\u{2029}'])
                    .map(|line| line.chars().count())
                    .max()
                    .unwrap_or(0);
                Ok(longest as f64 * char_width + 2.0 * self.code_block_padding().0)
            }
            AnyBlock::ImageBlock(img) => Ok(img.width.or(style.image_width).unwrap_or(0.0)),
            AnyBlock::HorizontalRule(_) | AnyBlock::RawHtmlBlock(_) => Ok(0.0),
        }
    }

    fn list_max_content(&self, items: &[ListItem]) -> Result<f64, String> {
        let mut widest = 0.0_f64;
        for item in items {
            let own = self.one_line_width(
                &self.build_text_runs(&item.spans, 0.0)?,
                self.style.base_font_size,
                Some("normal"),
                false,
            )?;
            widest = widest.max(own.max(self.blocks_max_content(&item.children)?));
        }
        Ok(self.style.list_indent + widest)
    }

    /// Lay `blocks` out once: the nodes and the extent they reserve. A caller
    /// that only wants the height reads `height`; there is no second pass.
    pub fn render_content(
        &self,
        blocks: &Document,
        width: f64,
        padding: f64,
    ) -> Result<RenderResult, String> {
        let (svg_elements, total_height) =
            self.render_blocks_to_elements(blocks, width, padding)?;
        Ok(RenderResult {
            elements: svg_elements,
            style: self.get_style_block(),
            width,
            height: total_height,
            max_content_width: self.max_content_width.get(),
        })
    }

    /// The scoped class name for bare rule `name` (e.g. "heading"). Mirrors
    /// `_scoped_class`.
    fn scoped_class(&self, name: &str) -> String {
        format!("md-{}-{}", self.class_prefix, name)
    }

    /// Measure each block's lines at `width`. The measure half of a
    /// measure-then-place pair — a caller that needs to fill fixed-height
    /// boxes measures once here, decides its own breaks, then draws runs of
    /// lines with `render_block_window` (`renderer.py::measure_blocks`).
    pub fn measure_blocks(
        &self,
        blocks: &Document,
        width: f64,
    ) -> Result<Vec<BlockMetrics>, String> {
        let ctx = RenderContext {
            x: 0.0,
            y: 0.0,
            width,
            indent: 0.0,
        };
        let mut metrics = Vec::with_capacity(blocks.len());
        let mut prev: Option<&AnyBlock> = None;

        for block in blocks {
            let gap = prev.map_or(0.0, |p| self.inter_block_gap(p, block));
            if let AnyBlock::Paragraph(p) = block {
                let lines = self.text_lines(
                    &p.spans,
                    width,
                    self.style.base_font_size,
                    "normal",
                    self.style.line_height,
                    false,
                )?;
                // Production reports one advance per line (`line_advances`);
                // `BlockMetrics` carries one for the block, so a line an inline
                // image makes taller than the rest refuses rather than packs.
                let line_advance = match lines.first() {
                    None => self.style.base_font_size * self.style.line_height,
                    Some(first) if lines.iter().all(|line| line.height == first.height) => first.height,
                    Some(_) => {
                        return Err(
                            "mdsvg-rs: inline image taller than its line in measure_blocks not supported".into(),
                        )
                    }
                };
                metrics.push(BlockMetrics {
                    line_count: lines.len().max(1),
                    line_advance,
                    space_before: gap,
                    splittable: true,
                    keep_with_next: false,
                    leading_margin: 0.0,
                });
            } else {
                let (_, height) = self.render_block(block, &ctx)?;
                metrics.push(BlockMetrics {
                    line_count: 1,
                    line_advance: height,
                    space_before: gap,
                    splittable: false,
                    keep_with_next: matches!(block, AnyBlock::Heading(_)),
                    leading_margin: self.heading_margins(block).0,
                });
            }
            prev = Some(block);
        }

        Ok(metrics)
    }

    /// Render `count` lines of `block`, beginning at line `start` (`count:
    /// None` means "to the end"). The placement half of the pair — the
    /// window is drawn at the top of its own coordinate box, so a
    /// continuation does not inherit the offset of the lines preceding it.
    /// An unsplittable block ignores the window and renders whole — asking
    /// for a slice of something indivisible is a caller error the caller
    /// can already avoid by reading `BlockMetrics::splittable`
    /// (`renderer.py::render_block_window`).
    pub fn render_block_window(
        &self,
        block: &AnyBlock,
        width: f64,
        start: usize,
        count: Option<usize>,
    ) -> Result<RenderResult, String> {
        let ctx = RenderContext {
            x: 0.0,
            y: 0.0,
            width,
            indent: 0.0,
        };
        let (elements, height) = if let AnyBlock::Paragraph(p) = block {
            self.render_text_block(
                &p.spans,
                &ctx,
                self.style.base_font_size,
                &self.scoped_class("text"),
                "normal",
                None,
                Some((start, count)),
                false,
            )?
        } else {
            self.render_block(block, &ctx)?
        };

        Ok(RenderResult {
            elements,
            style: self.get_style_block(),
            width,
            height,
            max_content_width: self.max_content_width.get(),
        })
    }

    fn get_style_block(&self) -> Element {
        let bodies = style_rule_bodies(&self.style);
        let mut rules = vec![ws("\n")];
        for name in STYLE_RULE_NAMES {
            let rule = format!("    .{} {{ {} }}", self.scoped_class(name), bodies[name]);
            rules.push(quoted(rule));
            rules.push(ws("\n"));
        }
        // The last newline already closes the final rule; the tag sits two spaces in.
        rules.pop();
        rules.push(ws("\n  "));
        el("style", vec![], rules)
    }

    /// Painted size of an inline-code run sitting in text of `host_font_size`.
    fn inline_code_size(&self, host_font_size: f64) -> f64 {
        if self.style.code_font_size != 0.0 {
            return self.style.code_font_size;
        }
        round_half(host_font_size * self.style.code_font_scale)
    }

    /// Painted size of a fenced code block. Scales off `base_font_size`
    /// rather than any host: a block sits inside nothing, so tying it to a
    /// surrounding size would make it jump around.
    fn block_code_size(&self) -> f64 {
        if self.style.code_font_size != 0.0 {
            return self.style.code_font_size;
        }
        round_half(self.style.base_font_size * self.style.code_font_scale)
    }

    /// SVG presentation attrs for a fenced code `<text>`, honoring the full
    /// code font overlay. `font_size` is already resolved by
    /// `block_code_size`; re-resolving the override here would be a second
    /// copy of that decision.
    fn code_text_attrs(&self, font_size: f64) -> Vec<(&'static str, String)> {
        let s = &self.style;
        let family = if s.code_font_family.is_empty() {
            &s.mono_font_family
        } else {
            &s.code_font_family
        };
        let weight = if s.code_font_weight.is_empty() {
            "400"
        } else {
            &s.code_font_weight
        };
        // An attribute value, not a CSS declaration: production
        // (`renderer.py:1448`) does not route the family through
        // `css_font_family_stack`.
        let mut attrs = vec![
            ("font-family", family.clone()),
            ("font-size", fmt(font_size)),
            ("font-weight", weight.to_string()),
        ];
        if !s.code_font_style.is_empty() {
            attrs.push(("font-style", s.code_font_style.clone()));
        }
        if !s.code_font_decoration.is_empty() {
            attrs.push(("text-decoration", s.code_font_decoration.clone()));
        }
        attrs
    }

    /// Widest line's raw text width (no padding) — the real painted extent,
    /// which 'show' overflow mode lets exceed the box.
    fn code_content_width(&self, lines: &[String], char_width: f64) -> f64 {
        lines.iter().map(|l| l.chars().count()).max().unwrap_or(0) as f64 * char_width
    }

    fn code_block_width(&self, content_width: f64, padding: f64, max_width: f64) -> f64 {
        max_width.min(content_width + (padding * 2.0))
    }

    fn code_block_padding(&self) -> (f64, f64) {
        let padding_x = self.style.code_block_padding;
        // WHY: code blocks need enough side padding for the border but less
        // vertical padding so tight prose rhythm does not clip surrounding lines.
        let padding_y = padding_x * (2.0 / 3.0);
        (padding_x, padding_y)
    }

    /// Split a code block into one `(text, color)` segment list per source
    /// line. Python colors the lines with Pygments when `style.code_highlight`
    /// is on and the fence names a language Pygments resolves; an unknown
    /// alias falls through to the plain block (`renderer.py:815-821`).
    /// `renderer.py::_code_line_height`: the glyph's own leading, rounded up to
    /// whole body lines.
    fn code_line_height(&self, font_size: f64) -> f64 {
        let body_line = self.style.base_font_size * self.style.line_height;
        (font_size * 1.4 / body_line).ceil() * body_line
    }

    fn logical_code_lines(&self, code: &CodeBlock) -> Result<Vec<Vec<CodeSegment>>, String> {
        if self.style.code_highlight {
            if let Some(language) = code.language.as_deref().filter(|l| !l.is_empty()) {
                if let Some(lines) = highlight_code(&code.code, language, &self.style.code_theme)? {
                    return Ok(lines);
                }
            }
        }
        Ok(code
            .code
            .split('\n')
            .map(|line| {
                if line.is_empty() {
                    Vec::new()
                } else {
                    vec![(line.to_string(), String::new())]
                }
            })
            .collect())
    }

    /// Render one code-block source line as a single `<text>`. `None` for a
    /// blank line — the caller still advances y but emits no element.
    fn render_code_line_element(
        &self,
        segments: &[CodeSegment],
        x: f64,
        y: f64,
        code_attrs: &[(&'static str, String)],
        default_fill: &str,
        clip_path: Option<&str>,
    ) -> Option<Element> {
        let text: String = segments.iter().map(|(t, _)| t.as_str()).collect();
        if text.is_empty() {
            return None;
        }

        let mut attrs = vec![("x", fmt(x)), ("y", fmt(y))];
        attrs.extend(code_attrs.iter().cloned());
        attrs.push(("xml:space", "preserve".to_string()));
        let colored = segments.iter().any(|(_, color)| !color.is_empty());
        if !colored {
            attrs.push(("fill", default_fill.to_string()));
        }
        if let Some(clip_path) = clip_path {
            attrs.push(("clip-path", clip_path.to_string()));
        }
        if !colored {
            let shown = apply_case(&text, &self.style.code_font_case);
            return Some(el("text", attrs, vec![quoted(shown)]));
        }

        let tspans = segments
            .iter()
            .filter(|(seg_text, _)| !seg_text.is_empty())
            .map(|(seg_text, color)| {
                let fill = if color.is_empty() {
                    default_fill
                } else {
                    color
                };
                let shown = apply_case(seg_text, &self.style.code_font_case);
                Child::Element(el(
                    "tspan",
                    vec![("fill", fill.to_string())],
                    vec![quoted(shown)],
                ))
            })
            .collect();
        Some(el("text", attrs, tspans))
    }

    fn code_inline_style_parts(&self, host_font_size: f64) -> Vec<String> {
        let s = &self.style;
        let family = if s.code_font_family.is_empty() {
            &s.mono_font_family
        } else {
            &s.code_font_family
        };
        let mut parts = vec![
            format!("font-family: {}", css_font_family_stack(family)),
            format!("fill: {}", s.code_color),
            format!(
                "font-size: {}px",
                fmt(self.inline_code_size(host_font_size))
            ),
        ];
        if !s.code_font_weight.is_empty() {
            parts.push(format!("font-weight: {}", s.code_font_weight));
        }
        if !s.code_font_style.is_empty() {
            parts.push(format!("font-style: {}", s.code_font_style));
        }
        if !s.code_font_decoration.is_empty() {
            parts.push(format!("text-decoration: {}", s.code_font_decoration));
        }
        parts
    }

    fn link_text_decoration_style(&self) -> Vec<&'static str> {
        if !self.style.link_underline {
            return Vec::new();
        }
        vec![
            "text-decoration: underline",
            "text-decoration-style: solid",
            "text-decoration-thickness: 1px",
            "text-underline-offset: 1px",
        ]
    }

    /// Returns `(margin_top, margin_bottom)` if block is a Heading, else
    /// `(0, 0)`.
    fn heading_margins(&self, block: &AnyBlock) -> (f64, f64) {
        match block {
            AnyBlock::Heading(h) => self.heading_margins_at(h.level),
            _ => (0.0, 0.0),
        }
    }

    fn heading_margins_at(&self, level: u8) -> (f64, f64) {
        let font_size = self.style.get_heading_size(level);
        let top_px = match self.style.minor_heading_margin_top_px {
            Some(minor) if level >= 3 => Some(minor),
            _ => self.style.heading_margin_top_px,
        };
        let margin_top = top_px.unwrap_or(font_size * self.style.heading_margin_top);
        let bottom_px = match self.style.h1_margin_bottom_px {
            Some(h1) if level == 1 => Some(h1),
            _ => self.style.heading_margin_bottom_px,
        };
        let margin_bottom = bottom_px.unwrap_or(font_size * self.style.heading_margin_bottom);
        (margin_top, margin_bottom)
    }

    /// `renderer.py::_heading_lines`: a heading's laid-out lines, the shift
    /// that moves them down inside their block, and the block's text extent.
    /// With `heading_grid_px` the block is a whole number of grid lines and the
    /// last baseline sits where body text's baseline sits in the last of them.
    fn heading_lines(&self, heading: &Heading, width: f64) -> Result<(Vec<Line>, f64, f64), String> {
        let style = &self.style;
        let multiplier = style.heading_line_height.unwrap_or(style.line_height);
        let lines = self.text_lines(
            &heading.spans,
            width,
            style.get_heading_size(heading.level),
            &style.heading_font_weight,
            multiplier,
            true,
        )?;
        let text_height: f64 = lines.iter().map(|line| line.height).sum();
        let grid = match style.heading_grid_px {
            Some(grid) if !lines.is_empty() => grid,
            _ => return Ok((lines, 0.0, text_height)),
        };
        let box_height = ((text_height / grid - 0.1 - 1e-9).ceil().max(1.0)) * grid;
        let last = &lines[lines.len() - 1];
        let natural_last_baseline = text_height - last.height + last.ascent;
        let body_baseline = self.baseline_offset(style.base_font_size, style.line_height);
        let shift = box_height - grid + body_baseline - natural_last_baseline;
        Ok((lines, shift, box_height))
    }

    /// `renderer.py::first_baseline`: how far below a document's top edge its
    /// first baseline sits, the number the first line was drawn with at
    /// `width`. A heading opening the document collapses its top margin, and
    /// an inline image taller than the text pushes the baseline down with it;
    /// a title band aligning controls to that line needs this exact number.
    /// The first block must be a heading or a paragraph.
    pub fn first_baseline(&self, blocks: &Document, width: f64) -> Result<f64, String> {
        let (spans, font_size, font_weight, multiplier, is_heading) = match blocks.first() {
            Some(AnyBlock::Heading(heading)) => {
                let (lines, shift, _) = self.heading_lines(heading, width)?;
                // An empty heading paints no text; its line box still has a baseline.
                return Ok(lines.first().map_or_else(
                    || {
                        self.baseline_offset(
                            self.style.get_heading_size(heading.level),
                            self.style.heading_line_height.unwrap_or(self.style.line_height),
                        )
                    },
                    |line| line.ascent + shift,
                ));
            }
            Some(AnyBlock::Paragraph(paragraph)) => (
                &paragraph.spans,
                self.style.base_font_size,
                "normal",
                self.style.line_height,
                false,
            ),
            Some(other) => {
                let block_type = match other {
                    AnyBlock::CodeBlock(_) => "CODE_BLOCK",
                    AnyBlock::Blockquote(_) => "BLOCKQUOTE",
                    AnyBlock::UnorderedList(_) => "UNORDERED_LIST",
                    AnyBlock::OrderedList(_) => "ORDERED_LIST",
                    AnyBlock::HorizontalRule(_) => "HORIZONTAL_RULE",
                    AnyBlock::Table(_) => "TABLE",
                    AnyBlock::ImageBlock(_) => "IMAGE",
                    AnyBlock::RawHtmlBlock(_) => "HTML_BLOCK",
                    AnyBlock::Paragraph(_) | AnyBlock::Heading(_) => unreachable!(),
                };
                return Err(format!(
                    "first_baseline needs a heading or paragraph, got BlockType.{block_type}"
                ));
            }
            None => return Err("mdsvg-rs: first_baseline of an empty document not supported".into()),
        };
        let lines = self.text_lines(spans, width, font_size, font_weight, multiplier, is_heading)?;
        // An empty heading paints no text; its line box still has a baseline.
        Ok(lines
            .first()
            .map_or(self.baseline_offset(font_size, multiplier), |line| line.ascent))
    }

    /// `renderer.py::heading_text_box`: the `(top, height)` of a heading's text
    /// inside its block at `width`, from the block's top edge with the top
    /// margin collapsed.
    pub fn heading_text_box(&self, heading: &Heading, width: f64) -> Result<(f64, f64), String> {
        let (lines, shift, _) = self.heading_lines(heading, width)?;
        Ok((shift, lines.iter().map(|line| line.height).sum()))
    }

    fn inter_block_gap(&self, prev: &AnyBlock, curr: &AnyBlock) -> f64 {
        let prev_is_h = matches!(prev, AnyBlock::Heading(_));
        let curr_is_h = matches!(curr, AnyBlock::Heading(_));
        if prev_is_h && curr_is_h {
            let (_, prev_bottom) = self.heading_margins(prev);
            return -prev_bottom;
        }
        if prev_is_h || curr_is_h {
            return 0.0;
        }
        self.style.paragraph_spacing
    }

    fn render_block(
        &self,
        block: &AnyBlock,
        ctx: &RenderContext,
    ) -> Result<(Vec<Element>, f64), String> {
        match block {
            AnyBlock::Paragraph(p) => self.render_paragraph(p, ctx),
            AnyBlock::Heading(h) => self.render_heading(h, ctx),
            AnyBlock::CodeBlock(c) => self.render_code_block(c, ctx),
            AnyBlock::Blockquote(b) => self.render_blockquote(b, ctx),
            AnyBlock::UnorderedList(ul) => self.render_unordered_list(ul, ctx),
            AnyBlock::OrderedList(ol) => self.render_ordered_list(ol, ctx),
            AnyBlock::HorizontalRule(_) => self.render_horizontal_rule(ctx),
            AnyBlock::Table(t) => self.render_table(t, ctx),
            AnyBlock::ImageBlock(i) => self.render_image_block(i, ctx),
            AnyBlock::RawHtmlBlock(raw) => self.render_raw_html_block(raw, ctx),
        }
    }

    /// `renderer.py::_get_image_size` (`:2425`): no size when the aspect ratio
    /// is enforced; a `data:` URI decoded from its own payload either way
    /// (`images.rs`); any other source only when sizes are fetched, which is
    /// not ported and refuses by name.
    fn get_image_size(&self, url: &str) -> Result<Option<ImageSize>, String> {
        if self.style.image_enforce_aspect_ratio {
            return Ok(None);
        }
        if is_data_uri(url) {
            return data_uri_image_size(url);
        }
        if self.fetch_image_sizes {
            return Err("mdsvg-rs: image size fetch not supported".into());
        }
        Ok(None)
    }

    /// `image_fallback_aspect_ratio`, refused where Python divides by zero or
    /// paints a negative box.
    fn fallback_aspect_ratio(&self) -> Result<f64, String> {
        let ratio = self.style.image_fallback_aspect_ratio;
        if ratio <= 0.0 {
            return Err("mdsvg-rs: image non-positive fallback aspect ratio not supported".into());
        }
        Ok(ratio)
    }

    /// `renderer.py::_render_image_block` (`:2224-2291`). Width is the authored
    /// `{width=}` else `style.image_width`, each capped to the column, else the
    /// column; height is the authored `{height=}` (never capped), else the
    /// decoded aspect ratio when a width was authored, else
    /// `style.image_height`, else the decoded aspect ratio, else width over
    /// `image_fallback_aspect_ratio`. The size is read first, so a corrupt
    /// `data:` payload raises even when both dimensions are authored.
    fn render_image_block(
        &self,
        img: &ImageBlock,
        ctx: &RenderContext,
    ) -> Result<(Vec<Element>, f64), String> {
        let actual_size = self.get_image_size(&img.url)?;
        let img_width = match (img.width, self.style.image_width) {
            (Some(w), _) | (None, Some(w)) => ctx.width.min(w),
            (None, None) => ctx.width,
        };
        let img_height = match (img.height, actual_size, self.style.image_height) {
            (Some(h), _, _) => h,
            (None, Some(size), _) if img.width.is_some() => img_width / size.aspect_ratio(),
            (None, _, Some(h)) => h,
            (None, Some(size), None) => img_width / size.aspect_ratio(),
            (None, None, None) => img_width / self.fallback_aspect_ratio()?,
        };
        Ok((
            self.image_elements(ctx.x, ctx.y, img_width, img_height, &img.url, &img.alt),
            img_height,
        ))
    }

    /// `renderer.py::_image_elements`: one `<image>`, plus a `<title>` for a
    /// non-empty alt — block or inline alike. The URL passes to `href`
    /// unmapped (no `image_url_mapper`).
    fn image_elements(
        &self,
        x: f64,
        y: f64,
        width: f64,
        height: f64,
        url: &str,
        alt: &str,
    ) -> Vec<Element> {
        let mut elements = vec![el(
            "image",
            vec![
                ("x", fmt(x)),
                ("y", fmt(y)),
                ("width", fmt(width)),
                ("height", fmt(height)),
                ("href", collapse_spaces(url)),
                (
                    "preserveAspectRatio",
                    self.style.image_preserve_aspect_ratio.clone(),
                ),
            ],
            vec![],
        )];
        if !alt.is_empty() {
            elements.push(el(
                "title",
                vec![],
                vec![quoted(collapse_spaces(alt))],
            ));
        }
        elements
    }

    /// `renderer.py::_render_horizontal_rule`. A hidden rule keeps the space
    /// it occupies; a dashed one is a stroked `<line>` centered on the same
    /// band the solid `<rect>` fills.
    fn render_horizontal_rule(&self, ctx: &RenderContext) -> Result<(Vec<Element>, f64), String> {
        let height = self.style.hr_height;
        let margin = self.style.paragraph_spacing;
        let y_pos = ctx.y + margin;
        let space = margin + height + margin;
        if !self.style.hr_visible {
            return Ok((vec![], space));
        }
        let element = if self.style.hr_dasharray.is_empty() {
            el(
                "rect",
                vec![
                    ("x", fmt(ctx.x)),
                    ("y", fmt(y_pos)),
                    ("width", fmt(ctx.width)),
                    ("height", fmt(height)),
                    ("fill", self.style.hr_color.clone()),
                ],
                vec![],
            )
        } else {
            let mid = y_pos + height / 2.0;
            el(
                "line",
                vec![
                    ("x1", fmt(ctx.x)),
                    ("y1", fmt(mid)),
                    ("x2", fmt(ctx.x + ctx.width)),
                    ("y2", fmt(mid)),
                    ("stroke", self.style.hr_color.clone()),
                    ("stroke-width", fmt(height)),
                    ("stroke-dasharray", self.style.hr_dasharray.clone()),
                ],
                vec![],
            )
        };
        Ok((vec![element], space))
    }

    /// `renderer.py::_render_table`: content-sized columns, a header
    /// background, an outer border, and column/row separator lines.
    fn render_table(
        &self,
        table: &Table,
        ctx: &RenderContext,
    ) -> Result<(Vec<Element>, f64), String> {
        let mut elements: Vec<Element> = Vec::new();

        let padding = self.style.table_cell_padding;
        let font_size = self.style.base_font_size;

        let col_widths = self.table_column_widths(table, ctx.width)?;
        let table_width = py_float_sum(&col_widths);

        let header_layout =
            self.measure_table_row_layout(&table.header, &col_widths, padding, font_size, true)?;
        let body_layouts: Vec<TableRowLayout> = table
            .rows
            .iter()
            .map(|row| self.measure_table_row_layout(row, &col_widths, padding, font_size, false))
            .collect::<Result<Vec<_>, String>>()?;

        let mut current_y = ctx.y;

        // Header background.
        elements.push(el(
            "rect",
            vec![
                ("x", fmt(ctx.x)),
                ("y", fmt(current_y)),
                ("width", fmt(table_width)),
                ("height", fmt(header_layout.row_height)),
                ("fill", self.style.table_header_background.clone()),
            ],
            vec![],
        ));

        self.render_table_row(
            &mut elements,
            &table.header,
            &header_layout.cell_lines,
            ctx.x,
            current_y,
            &col_widths,
            padding,
            font_size,
            true,
        )?;
        current_y += header_layout.row_height;

        for (row, row_layout) in table.rows.iter().zip(&body_layouts) {
            self.render_table_row(
                &mut elements,
                row,
                &row_layout.cell_lines,
                ctx.x,
                current_y,
                &col_widths,
                padding,
                font_size,
                false,
            )?;
            current_y += row_layout.row_height;
        }

        let total_height = current_y - ctx.y;
        if !self.style.table_border_visible {
            return Ok((elements, total_height));
        }
        let stroke = self.table_border_stroke();

        // Table border.
        let mut border = vec![
            ("x", fmt(ctx.x)),
            ("y", fmt(ctx.y)),
            ("width", fmt(table_width)),
            ("height", fmt(total_height)),
            ("fill", "none".to_string()),
        ];
        border.extend(stroke.iter().cloned());
        elements.push(el("rect", border, vec![]));

        // Column separators.
        let mut col_x = ctx.x;
        for col_width in &col_widths[..col_widths.len().saturating_sub(1)] {
            col_x += col_width;
            elements.push(table_gridline(col_x, ctx.y, col_x, current_y, &stroke));
        }

        // Row separators.
        let mut row_y = ctx.y;
        let mut row_heights = vec![header_layout.row_height];
        row_heights.extend(body_layouts.iter().map(|l| l.row_height));
        for row_height in row_heights {
            row_y += row_height;
            if row_y < current_y {
                elements.push(table_gridline(
                    ctx.x,
                    row_y,
                    ctx.x + table_width,
                    row_y,
                    &stroke,
                ));
            }
        }

        Ok((elements, total_height))
    }

    /// `renderer.py::_table_border_stroke`: the stroke attributes every table
    /// gridline shares. A width of 1 and a solid line add no attribute.
    fn table_border_stroke(&self) -> Vec<(&'static str, String)> {
        let mut stroke = vec![("stroke", self.style.table_border_color.clone())];
        if self.style.table_border_width != 1.0 {
            stroke.push(("stroke-width", fmt(self.style.table_border_width)));
        }
        if !self.style.table_border_dasharray.is_empty() {
            stroke.push((
                "stroke-dasharray",
                self.style.table_border_dasharray.clone(),
            ));
        }
        stroke
    }

    /// `renderer.py::_table_cell_runs`: forces bold widths for header cells.
    /// A cell's inline image refuses by name: production lays a cell out
    /// through `_layout_lines`/`_emit_lines`, whose image box this crate's
    /// table path does not carry.
    fn table_cell_runs(&self, cell: &TableCell, is_header: bool) -> Result<Vec<TextRun>, String> {
        if cell.spans.iter().any(|span| span.span_type == SpanType::Image) {
            return Err("mdsvg-rs: inline image in a table cell not supported".into());
        }
        let runs = self.build_text_runs(&cell.spans, 0.0)?;
        if !is_header {
            return Ok(runs);
        }
        Ok(runs
            .into_iter()
            .map(|run| TextRun { is_bold: true, ..run })
            .collect())
    }

    /// `renderer.py::_table_cell_lines_to_inner_svg`: inline spans for one
    /// wrapped table-cell line.
    fn table_cell_lines_to_inner_svg(&self, line_runs: &[TextRun], font_size: f64) -> Vec<Child> {
        let mut parts: Vec<Child> = Vec::new();
        for run in line_runs {
            if run.text.is_empty() {
                continue;
            }
            let raw_text = if run.is_code && !self.style.code_font_case.is_empty() {
                apply_case(&run.text, &self.style.code_font_case)
            } else {
                run.text.clone()
            };
            let text = quoted(collapse_spaces(&raw_text));
            let mut style_parts: Vec<String> = Vec::new();

            if run.is_bold {
                style_parts.push(format!("font-weight: {}", self.style.bold_font_weight));
            }
            if run.is_italic {
                style_parts.push("font-style: italic".to_string());
            }
            if run.is_code {
                style_parts.extend(self.code_inline_style_parts(font_size));
            }
            if run.is_link {
                style_parts.push(format!("fill: {}", self.style.link_color));
                style_parts.extend(self.link_text_decoration_style().into_iter().map(String::from));
            }

            let style_attr: Vec<(&str, String)> = if style_parts.is_empty() {
                Vec::new()
            } else {
                vec![("style", style_parts.join("; "))]
            };
            if run.is_link && run.url.is_some() {
                let url = run.url.as_deref().unwrap_or_default();
                let tspan = el("tspan", style_attr, vec![text]);
                parts.push(Child::Element(el(
                    "a",
                    vec![("href", collapse_spaces(&safe_href(url)))],
                    vec![Child::Element(tspan)],
                )));
            } else if !style_parts.is_empty() {
                parts.push(Child::Element(el("tspan", style_attr, vec![text])));
            } else {
                parts.push(text);
            }
        }
        parts
    }

    /// `renderer.py::_measure_table_row_layout`: wraps each cell and
    /// returns its rendered line layout.
    fn measure_table_row_layout(
        &self,
        row: &TableRow,
        col_widths: &[f64],
        padding: f64,
        font_size: f64,
        is_header: bool,
    ) -> Result<TableRowLayout, String> {
        let line_height = font_size * self.style.line_height;
        let mut cell_lines: Vec<Vec<Vec<TextRun>>> = Vec::new();
        let mut max_lines = 1usize;

        for (cell, col_width) in row.cells.iter().zip(col_widths) {
            let max_width = (col_width - (padding * 2.0)).max(1.0);
            let runs = self.table_cell_runs(cell, is_header)?;
            let mut wrapped = self.wrap_runs(&runs, max_width, font_size, None, false)?;
            if wrapped.is_empty() {
                wrapped = vec![Vec::new()];
            }
            max_lines = max_lines.max(wrapped.len());
            cell_lines.push(wrapped);
        }

        let row_height = (max_lines as f64 * line_height) + (padding * 2.0);
        Ok(TableRowLayout { cell_lines, row_height })
    }

    /// Sums measured widths of a run sequence, for content-width tracking.
    /// Rust-only: Python's `_emit_line` sums the same per-run measurements
    /// inline.
    fn run_sequence_width(&self, runs: &[TextRun], font_size: f64) -> Result<f64, String> {
        let mut total = 0.0;
        for run in runs {
            if run.text.is_empty() {
                continue;
            }
            total += self.measure_text(
                &run.text,
                font_size,
                run.is_bold,
                run.is_italic,
                run.is_code,
                None,
                false,
            )?;
        }
        Ok(total)
    }

    /// `renderer.py::_render_table_row`.
    #[allow(clippy::too_many_arguments)]
    fn render_table_row(
        &self,
        elements: &mut Vec<Element>,
        row: &TableRow,
        cell_lines: &[Vec<Vec<TextRun>>],
        start_x: f64,
        y: f64,
        col_widths: &[f64],
        padding: f64,
        font_size: f64,
        is_header: bool,
    ) -> Result<(), String> {
        let mut x = start_x;
        let line_height = font_size * self.style.line_height;

        for (idx, cell) in row.cells.iter().enumerate() {
            let line_runs = &cell_lines[idx];
            let col_width = col_widths[idx];

            let (text_x, anchor) = match cell.align.as_deref() {
                Some("center") => (x + col_width / 2.0, "middle"),
                Some("right") => (x + col_width - padding, "end"),
                _ => (x + padding, "start"),
            };

            let css_class = self.scoped_class("text");
            let weight = if is_header {
                self.style.bold_font_weight.as_str()
            } else {
                "normal"
            };

            let mut text_y = y + padding + font_size;
            for runs in line_runs {
                let inner = self.table_cell_lines_to_inner_svg(runs, font_size);
                elements.push(text_el(
                    vec![
                        ("x", fmt(text_x)),
                        ("y", fmt(text_y)),
                        ("class", css_class.clone()),
                        ("font-size", fmt(font_size)),
                        ("font-weight", weight.to_string()),
                        ("text-anchor", anchor.to_string()),
                    ],
                    inner,
                ));
                // Python's `_emit_line` has no exception guard around its run
                // measurements either — an unmeasurable glyph aborts the whole
                // render there too, so this propagates rather than
                // substituting a 0-width fallback.
                let line_width = self.run_sequence_width(runs, font_size)?;
                let right_edge = match anchor {
                    "middle" => text_x + line_width / 2.0,
                    "end" => text_x,
                    _ => text_x + line_width,
                };
                self.max_content_width.set(self.max_content_width.get().max(right_edge));
                text_y += line_height;
            }

            x += col_width;
        }
        Ok(())
    }

    fn render_paragraph(
        &self,
        para: &Paragraph,
        ctx: &RenderContext,
    ) -> Result<(Vec<Element>, f64), String> {
        self.render_text_block(
            &para.spans,
            ctx,
            self.style.base_font_size,
            &self.scoped_class("text"),
            "normal",
            None,
            None,
            false,
        )
    }

    /// `renderer.py::_render_raw_html_block` with `allow_raw_html=False`: the
    /// block's source is one plain `Span`, so it is never parsed for markdown
    /// and reaches the SVG through the same escaping as any paragraph text.
    fn render_raw_html_block(
        &self,
        raw: &RawHtmlBlock,
        ctx: &RenderContext,
    ) -> Result<(Vec<Element>, f64), String> {
        self.render_paragraph(
            &Paragraph {
                spans: vec![Span::text(raw.html.as_str())],
            },
            ctx,
        )
    }

    fn render_heading(
        &self,
        heading: &Heading,
        ctx: &RenderContext,
    ) -> Result<(Vec<Element>, f64), String> {
        let font_size = self.style.get_heading_size(heading.level);
        let (margin_top, margin_bottom) = self.heading_margins_at(heading.level);

        let font_weight = &self.style.heading_font_weight;
        let (lines, shift, box_height) = self.heading_lines(heading, ctx.width)?;
        let (elements, _) = self.paint_text_lines(
            &lines,
            &ctx.with_offset(margin_top + shift),
            font_size,
            &self.scoped_class("heading"),
            font_weight,
            true,
        )?;

        Ok((elements, margin_top + box_height + margin_bottom))
    }

    /// Render a code block with background.
    fn render_code_block(
        &self,
        code: &CodeBlock,
        ctx: &RenderContext,
    ) -> Result<(Vec<Element>, f64), String> {
        use crate::style::CodeBlockOverflow::*;
        match self.style.code_block_overflow {
            Wrap => self.render_code_block_wrapped(code, ctx),
            Show => self.render_code_block_simple(code, ctx, "show"),
            Hide => self.render_code_block_simple(code, ctx, "hide"),
            Ellipsis => self.render_code_block_simple(code, ctx, "ellipsis"),
        }
    }

    /// The fenced-code-block background rect, with optional border stroke.
    fn render_code_block_background(
        &self,
        ctx: &RenderContext,
        block_width: f64,
        total_height: f64,
    ) -> Element {
        let mut attrs = vec![
            ("x", fmt(ctx.x)),
            ("y", fmt(ctx.y)),
            ("width", fmt(block_width)),
            ("height", fmt(total_height)),
            ("fill", self.style.code_background.clone()),
            ("rx", fmt(self.style.code_block_border_radius)),
        ];
        let border_color = &self.style.code_block_border_color;
        if !border_color.is_empty() {
            attrs.push(("stroke", border_color.clone()));
            attrs.push(("stroke-width", fmt(self.style.code_block_border_width)));
        }
        el("rect", attrs, vec![])
    }

    /// Render code block with show/hide/ellipsis overflow.
    fn render_code_block_simple(
        &self,
        code: &CodeBlock,
        ctx: &RenderContext,
        overflow: &str,
    ) -> Result<(Vec<Element>, f64), String> {
        let mut elements: Vec<Element> = Vec::new();

        let (padding_x, padding_y) = self.code_block_padding();
        let font_size = self.block_code_size();
        let line_height = self.code_line_height(font_size);
        let char_width = font_size * self.ensure_mono_char_width()?;
        let max_chars = ((ctx.width - padding_x * 2.0) / char_width) as i64;

        let mut lines = self.logical_code_lines(code)?;

        if overflow == "ellipsis" {
            lines = lines
                .into_iter()
                .map(|segments| {
                    let line_len: i64 =
                        segments.iter().map(|(t, _)| t.chars().count() as i64).sum();
                    if line_len > max_chars && max_chars > 3 {
                        let mut truncated = truncate_segments(&segments, max_chars - 3);
                        truncated.push(("...".to_string(), String::new()));
                        truncated
                    } else {
                        segments
                    }
                })
                .collect();
        }

        let plain_lines: Vec<String> = lines
            .iter()
            .map(|segments| segments.iter().map(|(t, _)| t.as_str()).collect())
            .collect();

        let text_height = lines.len() as f64 * line_height;
        let total_height = text_height + (padding_y * 2.0);
        let content_width = self.code_content_width(&plain_lines, char_width);
        self.max_content_width.set(
            self.max_content_width
                .get()
                .max(ctx.x + padding_x + content_width),
        );
        let block_width = self.code_block_width(content_width, padding_x, ctx.width);

        let mut clip_id: Option<String> = None;
        if overflow == "hide" {
            // Content + geometry, not a pointer address — the address is
            // nondeterministic between runs and can collide when two renders
            // share one document (address reuse). Geometry disambiguates two
            // identical code blocks rendered at different positions in the
            // same document. Mirrors Python's clip_hash exactly.
            let clip_hash = sha256_hex8(
                &[
                    code.code.as_str(),
                    code.language.as_deref().unwrap_or(""),
                    &fmt(ctx.x),
                    &fmt(ctx.y),
                    &fmt(block_width),
                    &fmt(total_height),
                ]
                .join("\u{0}"),
            );
            let id = format!("code-clip-{clip_hash}");
            let clip_rect = el(
                "rect",
                vec![
                    ("x", fmt(ctx.x)),
                    ("y", fmt(ctx.y)),
                    ("width", fmt(block_width)),
                    ("height", fmt(total_height)),
                ],
                vec![],
            );
            let clip_path = el(
                "clipPath",
                vec![("id", id.clone())],
                vec![Child::Element(clip_rect)],
            );
            elements.push(el("defs", vec![], vec![Child::Element(clip_path)]));
            clip_id = Some(id);
        }

        elements.push(self.render_code_block_background(ctx, block_width, total_height));

        let clip_path = clip_id.as_ref().map(|id| format!("url(#{id})"));
        let code_fill = if self.style.code_color.is_empty() {
            &self.style.text_color
        } else {
            &self.style.code_color
        };
        let code_attrs = self.code_text_attrs(font_size);
        let mut y_offset = ctx.y + padding_y + font_size;
        for segments in &lines {
            if let Some(rendered) = self.render_code_line_element(
                segments,
                ctx.x + padding_x,
                y_offset,
                &code_attrs,
                code_fill,
                clip_path.as_deref(),
            ) {
                elements.push(rendered);
            }
            y_offset += line_height;
        }

        Ok((elements, total_height))
    }

    /// Render code block with wrapped lines.
    fn render_code_block_wrapped(
        &self,
        code: &CodeBlock,
        ctx: &RenderContext,
    ) -> Result<(Vec<Element>, f64), String> {
        let mut elements: Vec<Element> = Vec::new();

        let (padding_x, padding_y) = self.code_block_padding();
        let font_size = self.block_code_size();
        let line_height = self.code_line_height(font_size);
        let char_width = font_size * self.ensure_mono_char_width()?;
        let max_chars = (10i64).max(((ctx.width - padding_x * 2.0) / char_width) as i64);

        let mut wrapped_lines: Vec<Vec<CodeSegment>> = Vec::new();
        for segments in self.logical_code_lines(code)? {
            wrapped_lines.extend(wrap_colored_line(&segments, max_chars));
        }

        let plain_lines: Vec<String> = wrapped_lines
            .iter()
            .map(|segments| segments.iter().map(|(t, _)| t.as_str()).collect())
            .collect();

        let text_height = wrapped_lines.len() as f64 * line_height;
        let total_height = text_height + (padding_y * 2.0);
        let content_width = self.code_content_width(&plain_lines, char_width);
        self.max_content_width.set(
            self.max_content_width
                .get()
                .max(ctx.x + padding_x + content_width),
        );
        let block_width = self.code_block_width(content_width, padding_x, ctx.width);

        elements.push(self.render_code_block_background(ctx, block_width, total_height));

        let code_fill = if self.style.code_color.is_empty() {
            &self.style.text_color
        } else {
            &self.style.code_color
        };
        let code_attrs = self.code_text_attrs(font_size);
        let mut y_offset = ctx.y + padding_y + font_size;
        for segments in &wrapped_lines {
            if let Some(rendered) = self.render_code_line_element(
                segments,
                ctx.x + padding_x,
                y_offset,
                &code_attrs,
                code_fill,
                None,
            ) {
                elements.push(rendered);
            }
            y_offset += line_height;
        }

        Ok((elements, total_height))
    }

    /// Render a blockquote paragraph using the md-blockquote CSS class.
    /// Case transform is applied here so `blockquote_font_case` is honored.
    fn render_blockquote_paragraph(
        &self,
        para: &Paragraph,
        ctx: &RenderContext,
    ) -> Result<(Vec<Element>, f64), String> {
        let (spans, bq_font_size) = self.blockquote_text(para);
        self.render_text_block(
            &spans,
            ctx,
            bq_font_size,
            &self.scoped_class("blockquote"),
            "normal",
            None,
            None,
            false,
        )
    }

    /// `renderer.py::_blockquote_text`: a blockquote paragraph's spans, case
    /// transform applied, and the size it paints at.
    fn blockquote_text(&self, para: &Paragraph) -> (Vec<Span>, f64) {
        let bq_case = &self.style.blockquote_font_case;
        let spans = if bq_case.is_empty() {
            para.spans.clone()
        } else {
            para.spans
                .iter()
                .map(|s| Span {
                    text: apply_case(&s.text, bq_case),
                    ..s.clone()
                })
                .collect()
        };
        let size = if self.style.blockquote_font_size != 0.0 {
            self.style.blockquote_font_size
        } else {
            self.style.base_font_size
        };
        (spans, size)
    }

    /// Render a blockquote with left border.
    fn render_blockquote(
        &self,
        bq: &Blockquote,
        ctx: &RenderContext,
    ) -> Result<(Vec<Element>, f64), String> {
        let mut elements: Vec<Element> = Vec::new();

        let indent = self.style.blockquote_padding;
        let inner_ctx = ctx.with_indent(indent);

        let mut current_y = 0.0;
        let mut inner_elements: Vec<Element> = Vec::new();
        let mut prev_block: Option<&AnyBlock> = None;

        for block in &bq.blocks {
            if let Some(prev) = prev_block {
                current_y += self.inter_block_gap(prev, block);
            }
            // A blockquote is a box like any other, so the block that opens
            // it collapses its leading margin the same way — see
            // render_blocks_to_elements, which states the rule.
            let lift = if prev_block.is_none() {
                self.heading_margins(block).0
            } else {
                0.0
            };
            let (block_elements, height) = if let AnyBlock::Paragraph(p) = block {
                self.render_blockquote_paragraph(p, &inner_ctx.with_offset(current_y - lift))?
            } else {
                self.render_block(block, &inner_ctx.with_offset(current_y - lift))?
            };
            inner_elements.extend(block_elements);
            current_y += height - lift;
            prev_block = Some(block);
        }

        let total_height = current_y;

        let bg_color = &self.style.blockquote_background;
        if !bg_color.is_empty() {
            let rx = self.style.blockquote_border_radius;
            let mut attrs = vec![
                ("x", fmt(ctx.x)),
                ("y", fmt(ctx.y)),
                ("width", fmt(ctx.width)),
                ("height", fmt(total_height)),
                ("fill", bg_color.clone()),
            ];
            if rx != 0.0 {
                attrs.push(("rx", fmt(rx)));
            }
            elements.push(el("rect", attrs, vec![]));
        }

        elements.push(el(
            "rect",
            vec![
                ("x", fmt(ctx.x)),
                ("y", fmt(ctx.y)),
                ("width", fmt(self.style.blockquote_border_width)),
                ("height", fmt(total_height)),
                ("fill", self.style.blockquote_border_color.clone()),
            ],
            vec![],
        ));

        elements.extend(inner_elements);

        Ok((elements, total_height))
    }

    /// Render nested child blocks (sub-lists, etc.) below an item's own text.
    ///
    /// Returns `(elements, total_height)` where `total_height` includes
    /// `item_height` plus any children. A childless item returns `([], 0.0)`
    /// so flat lists are byte-identical to the pre-children implementation.
    fn render_item_children(
        &self,
        item_ctx: &RenderContext,
        item_height: f64,
        children: &[AnyBlock],
    ) -> Result<(Vec<Element>, f64), String> {
        if children.is_empty() {
            return Ok((Vec::new(), 0.0));
        }
        let mut child_elements: Vec<Element> = Vec::new();
        let mut child_y = item_height;
        for child_block in children {
            let (c_elements, c_height) =
                self.render_block(child_block, &item_ctx.with_offset(child_y))?;
            child_elements.extend(c_elements);
            child_y += c_height;
        }
        Ok((child_elements, child_y - item_height))
    }

    /// `renderer.py::_item_lines`: a list item's lines, and how far its first
    /// baseline sits below a plain text line's — an inline image makes that
    /// line taller, and the item's marker moves down with the words it labels.
    fn item_lines(&self, item: &ListItem, width: f64) -> Result<(Vec<Line>, f64), String> {
        let font_size = self.style.base_font_size;
        let lines = self.text_lines(&item.spans, width, font_size, "normal", self.style.line_height, false)?;
        let drop = match lines.first() {
            Some(first) => first.ascent - self.baseline_offset(font_size, self.style.line_height),
            None => 0.0,
        };
        Ok((lines, drop))
    }

    /// Render an unordered list.
    fn render_unordered_list(
        &self,
        ul: &UnorderedList,
        ctx: &RenderContext,
    ) -> Result<(Vec<Element>, f64), String> {
        let mut elements: Vec<Element> = Vec::new();
        let mut current_y = 0.0;

        let bullet_indent = self.style.list_indent;

        for item in &ul.items {
            // WHY: journalism-style prose lists read quieter with a small
            // marker and a tight, stable gap from marker edge to text.
            let bullet_radius: f64 = 1.5;
            let bullet_gap: f64 = 6.5;
            let bullet_x = ctx.x + bullet_indent - bullet_gap - bullet_radius;
            let item_ctx = ctx.with_indent(bullet_indent).with_offset(current_y);
            let (lines, drop) = self.item_lines(item, item_ctx.width)?;
            // Half a line box down is also the center of the line's text: the
            // half-leading above the ascent and below the descent are equal,
            // so the two midpoints coincide.
            let bullet_y = ctx.y
                + current_y
                + (self.style.base_font_size * self.style.line_height / 2.0)
                + drop;

            elements.push(el(
                "circle",
                vec![
                    ("cx", fmt(bullet_x)),
                    ("cy", fmt(bullet_y)),
                    ("r", fmt(bullet_radius)),
                    ("fill", self.style.text_color.clone()),
                ],
                vec![],
            ));

            let (item_elements, item_height) = self.paint_text_lines(
                &lines,
                &item_ctx,
                self.style.base_font_size,
                &self.scoped_class("text"),
                "normal",
                false,
            )?;
            elements.extend(item_elements);

            let (child_elements, child_height) =
                self.render_item_children(&item_ctx, item_height, &item.children)?;
            elements.extend(child_elements);

            current_y += item_height + child_height + self.style.list_item_spacing;
        }

        if !ul.items.is_empty() {
            current_y -= self.style.list_item_spacing;
        }

        Ok((elements, current_y))
    }

    /// Render an ordered list.
    fn render_ordered_list(
        &self,
        ol: &OrderedList,
        ctx: &RenderContext,
    ) -> Result<(Vec<Element>, f64), String> {
        let mut elements: Vec<Element> = Vec::new();
        let mut current_y = 0.0;

        let bullet_indent = self.style.list_indent;

        for (idx, item) in ol.items.iter().enumerate() {
            let number = ol.start + idx as i64;

            let number_text = format!("{number}.");
            let number_x = ctx.x + bullet_indent - 8.0;
            let item_ctx = ctx.with_indent(bullet_indent).with_offset(current_y);
            let (lines, drop) = self.item_lines(item, item_ctx.width)?;
            // The same expression render_text_block uses for the item's own
            // first baseline, so a marker and the words it labels sit on one
            // line by construction rather than by two formulas agreeing.
            let number_y = ctx.y
                + current_y
                + self.baseline_offset(self.style.base_font_size, self.style.line_height)
                + drop;

            let number_class = self.scoped_class("text");
            elements.push(text_el(
                vec![
                    ("x", fmt(number_x)),
                    ("y", fmt(number_y)),
                    ("class", number_class),
                    ("font-size", fmt(self.style.base_font_size)),
                    ("text-anchor", "end".to_string()),
                ],
                vec![quoted(number_text)],
            ));

            let (item_elements, item_height) = self.paint_text_lines(
                &lines,
                &item_ctx,
                self.style.base_font_size,
                &self.scoped_class("text"),
                "normal",
                false,
            )?;
            elements.extend(item_elements);

            let (child_elements, child_height) =
                self.render_item_children(&item_ctx, item_height, &item.children)?;
            elements.extend(child_elements);

            current_y += item_height + child_height + self.style.list_item_spacing;
        }

        if !ol.items.is_empty() {
            current_y -= self.style.list_item_spacing;
        }

        Ok((elements, current_y))
    }

    /// How far below a line box's top edge that line's baseline sits.
    fn baseline_offset(&self, font_size: f64, line_height_multiplier: f64) -> f64 {
        let ascent = self.measurer.ascent_em() * font_size;
        let descent = self.measurer.descent_em() * font_size;
        let half_leading = (font_size * line_height_multiplier - ascent - descent) / 2.0;
        half_leading + ascent
    }

    /// `line_window` restricts output to `(start, count)` of the wrapped
    /// lines, drawn at the top of the box rather than at their original
    /// offsets — `count: None` means "to the end". Wrapping itself is
    /// unaffected: the same break positions are chosen either way, so a
    /// windowed render is a slice of the whole, never a reflow
    /// (`renderer.py::_render_text_block`'s own `line_window` contract).
    #[allow(clippy::too_many_arguments)]
    fn render_text_block(
        &self,
        spans: &[Span],
        ctx: &RenderContext,
        font_size: f64,
        css_class: &str,
        font_weight: &str,
        line_height_multiplier: Option<f64>,
        line_window: Option<(usize, Option<usize>)>,
        is_heading: bool,
    ) -> Result<(Vec<Element>, f64), String> {
        if spans.is_empty() {
            return Ok((Vec::new(), 0.0));
        }
        let multiplier = line_height_multiplier.unwrap_or(self.style.line_height);
        let mut lines =
            self.text_lines(spans, ctx.width, font_size, font_weight, multiplier, is_heading)?;
        if let Some((start, count)) = line_window {
            let end = count.map_or(lines.len(), |c| (start + c).min(lines.len()));
            lines = if start >= lines.len() {
                Vec::new()
            } else {
                lines[start..end].to_vec()
            };
        }
        self.paint_text_lines(&lines, ctx, font_size, css_class, font_weight, is_heading)
    }

    /// `renderer.py::_text_lines`: prose lines — paragraphs, headings, list
    /// items — at `width`, each inline image sized for it.
    fn text_lines(
        &self,
        spans: &[Span],
        width: f64,
        font_size: f64,
        font_weight: &str,
        line_height_multiplier: f64,
        is_heading: bool,
    ) -> Result<Vec<Line>, String> {
        let runs = self.build_text_runs(spans, width)?;
        let text_ascent = self.baseline_offset(font_size, line_height_multiplier);
        let line_height = font_size * line_height_multiplier;
        let wrapped = self.wrap_runs(&runs, width, font_size, Some(font_weight), is_heading)?;
        // `renderer.py::_layout_lines`: an inline image's bottom sits on the
        // baseline, so one taller than the text ascent grows its line upward.
        Ok(wrapped
            .into_iter()
            .map(|runs| {
                let ascent = runs
                    .iter()
                    .filter_map(|run| run.image.as_ref().map(|image| image.height))
                    .fold(text_ascent, f64::max);
                Line {
                    runs,
                    ascent,
                    height: line_height + (ascent - text_ascent),
                }
            })
            .collect())
    }

    /// `renderer.py::_paint_text_lines` + `_emit_lines`: paint `lines` downward
    /// from the block's top at its text alignment; return the height taken.
    fn paint_text_lines(
        &self,
        lines: &[Line],
        ctx: &RenderContext,
        font_size: f64,
        css_class: &str,
        font_weight: &str,
        is_heading: bool,
    ) -> Result<(Vec<Element>, f64), String> {
        let anchor = self.style.get_text_anchor();
        let anchor_x = match self.style.text_align {
            crate::style::TextAlign::Center => ctx.x + ctx.width / 2.0,
            crate::style::TextAlign::Right => ctx.x + ctx.width,
            crate::style::TextAlign::Left => ctx.x,
        };
        let text_attrs = vec![
            ("font-size", fmt(font_size)),
            ("class", css_class.to_string()),
        ];
        let mut elements: Vec<Element> = Vec::new();
        let mut line_top = ctx.y;
        for line in lines {
            self.emit_line(
                &mut elements,
                &line.runs,
                anchor_x,
                anchor,
                line_top + line.ascent,
                font_size,
                font_weight,
                is_heading,
                &text_attrs,
            )?;
            line_top += line.height;
        }
        let heights: Vec<f64> = lines.iter().map(|line| line.height).collect();
        Ok((elements, py_float_sum(&heights)))
    }

    /// `renderer.py::_emit_line`: one line's code chips, then its text on
    /// `baseline` at `anchor_x`. SVG has no `<image>` inside `<text>`, so a
    /// line holding an image walks left to right, emitting one start-anchored
    /// `<text>` per stretch of text runs and an `<image>` per image run,
    /// bottom-aligned to the baseline.
    #[allow(clippy::too_many_arguments)]
    fn emit_line(
        &self,
        elements: &mut Vec<Element>,
        runs: &[TextRun],
        anchor_x: f64,
        anchor: &str,
        baseline: f64,
        font_size: f64,
        font_weight: &str,
        is_heading: bool,
        text_attrs: &[(&'static str, String)],
    ) -> Result<(), String> {
        let mut widths: Vec<f64> = Vec::with_capacity(runs.len());
        for run in runs {
            widths.push(match &run.image {
                Some(image) => image.width,
                None if run.text.is_empty() => 0.0,
                None => {
                    let display_text = if run.is_code && !self.style.code_font_case.is_empty() {
                        apply_case(&run.text, &self.style.code_font_case)
                    } else {
                        run.text.clone()
                    };
                    self.measure_text(
                        &display_text,
                        font_size,
                        run.is_bold,
                        run.is_italic,
                        run.is_code,
                        Some(font_weight),
                        is_heading,
                    )?
                }
            });
        }
        let line_width = py_sum(runs.iter().zip(&widths).map(|(run, &width)| match &run.image {
            Some(image) => image.width_addend(),
            None => PyNum::Float(width),
        }));
        let left = match anchor {
            "middle" => anchor_x - line_width / 2.0,
            "end" => anchor_x - line_width,
            _ => anchor_x,
        };
        self.max_content_width
            .set(self.max_content_width.get().max(left + line_width));

        self.emit_code_chips(elements, runs, &widths, left, baseline, font_size);

        if runs.iter().all(|run| run.image.is_none()) {
            elements.push(self.line_text(
                anchor_x,
                baseline,
                text_attrs,
                anchor,
                runs,
                font_size,
                font_weight,
            ));
            return Ok(());
        }

        let mut segment: Vec<TextRun> = Vec::new();
        let mut segment_x = left;
        let mut run_x = left;
        let flush = |segment: &mut Vec<TextRun>, segment_x: f64, elements: &mut Vec<Element>| {
            if !segment.is_empty() {
                elements.push(self.line_text(
                    segment_x,
                    baseline,
                    text_attrs,
                    "start",
                    segment,
                    font_size,
                    font_weight,
                ));
                segment.clear();
            }
        };
        for (run, width) in runs.iter().zip(&widths) {
            if let Some(image) = &run.image {
                flush(&mut segment, segment_x, elements);
                elements.extend(self.image_elements(
                    run_x,
                    baseline - image.height,
                    image.width,
                    image.height,
                    &image.url,
                    &image.alt,
                ));
            } else if segment.is_empty() {
                let (stripped, x) = self.strip_segment_leading_space(
                    run,
                    run_x,
                    font_size,
                    font_weight,
                    is_heading,
                )?;
                segment_x = x;
                segment.push(stripped);
            } else {
                segment.push(run.clone());
            }
            run_x += width;
        }
        flush(&mut segment, segment_x, elements);
        Ok(())
    }

    /// One `<text>` of `runs` at (`x`, `baseline`), after the block's shared attributes.
    #[allow(clippy::too_many_arguments)]
    fn line_text(
        &self,
        x: f64,
        baseline: f64,
        text_attrs: &[(&'static str, String)],
        anchor: &str,
        runs: &[TextRun],
        font_size: f64,
        font_weight: &str,
    ) -> Element {
        let mut attrs = vec![("x", fmt(x)), ("y", fmt(baseline))];
        attrs.extend(text_attrs.iter().cloned());
        attrs.push(("text-anchor", anchor.to_string()));
        text_el(attrs, self.tspan_content(runs, font_size, font_weight))
    }

    /// `renderer.py::_emit_code_chips`: background boxes behind a line's
    /// inline-code runs, before its text. The chip spans cap height above the
    /// baseline to the descender below, plus padding on each side.
    fn emit_code_chips(
        &self,
        elements: &mut Vec<Element>,
        runs: &[TextRun],
        widths: &[f64],
        left: f64,
        baseline: f64,
        font_size: f64,
    ) {
        let chip_padding = self.style.inline_code_padding;
        let chip_radius = self.style.inline_code_border_radius;
        let code_font_size = self.inline_code_size(font_size);
        let chip_height =
            (CHIP_CAP_HEIGHT_RATIO + CHIP_DESCENDER_RATIO) * code_font_size + 2.0 * chip_padding;
        let mut run_x = left;
        for (run, rw) in runs.iter().zip(widths) {
            if run.is_code && *rw > 0.0 {
                let mut attrs = vec![
                    ("x", fmt(run_x - chip_padding)),
                    (
                        "y",
                        fmt(baseline - CHIP_CAP_HEIGHT_RATIO * code_font_size - chip_padding),
                    ),
                    ("width", fmt(rw + 2.0 * chip_padding)),
                    ("height", fmt(chip_height)),
                    ("fill", self.style.code_background.clone()),
                ];
                if chip_radius != 0.0 {
                    attrs.push(("rx", fmt(chip_radius)));
                }
                elements.push(el("rect", attrs, vec![]));
            }
            run_x += rw;
        }
    }

    /// `renderer.py::_strip_segment_leading_space`: a text segment opening
    /// after an inline image (or opening its line) can carry the wrap's
    /// separator as a leading space. SVG collapses a `<text>`'s leading
    /// whitespace on screen but layout measured it, so it is stripped and
    /// the segment starts one space-width later.
    fn strip_segment_leading_space(
        &self,
        run: &TextRun,
        segment_x: f64,
        font_size: f64,
        font_weight: &str,
        is_heading: bool,
    ) -> Result<(TextRun, f64), String> {
        let stripped = run.text.trim_start_matches(' ');
        if stripped.len() == run.text.len() {
            return Ok((run.clone(), segment_x));
        }
        let leading_ws = &run.text[..run.text.len() - stripped.len()];
        let ws_width = self.measure_text(
            leading_ws,
            font_size,
            run.is_bold,
            run.is_italic,
            run.is_code,
            Some(font_weight),
            is_heading,
        )?;
        Ok((run.with_text(stripped), segment_x + ws_width))
    }

    /// `renderer.py::_tspan_content`: a stretch of text runs as
    /// `<tspan>`/`<a>` inner nodes.
    fn tspan_content(&self, runs: &[TextRun], font_size: f64, font_weight: &str) -> Vec<Child> {
        let mut tspan_parts: Vec<Child> = Vec::new();
        for run in runs {
            if run.text.is_empty() {
                continue;
            }
            // Case transform runs on raw text, before escaping, so entities survive.
            let raw_text = if run.is_code && !self.style.code_font_case.is_empty() {
                apply_case(&run.text, &self.style.code_font_case)
            } else {
                run.text.clone()
            };
            let text = quoted(collapse_spaces(&raw_text));

            let mut style_parts: Vec<String> = Vec::new();
            if run.is_bold {
                style_parts.push(format!("font-weight: {}", self.style.bold_font_weight));
            } else if font_weight != "normal" {
                style_parts.push(format!("font-weight: {font_weight}"));
            }
            if run.is_italic {
                style_parts.push("font-style: italic".to_string());
            }
            if run.is_code {
                style_parts.extend(self.code_inline_style_parts(font_size));
            }
            if run.is_link {
                style_parts.push(format!("fill: {}", self.style.link_color));
                style_parts.extend(
                    self.link_text_decoration_style()
                        .into_iter()
                        .map(str::to_string),
                );
            }

            let style_attr: Vec<(&str, String)> = if style_parts.is_empty() {
                Vec::new()
            } else {
                vec![("style", style_parts.join("; "))]
            };

            match &run.url {
                Some(url) if run.is_link => {
                    let tspan = el("tspan", style_attr, vec![text]);
                    tspan_parts.push(Child::Element(el(
                        "a",
                        vec![("href", collapse_spaces(&safe_href(url)))],
                        vec![Child::Element(tspan)],
                    )));
                }
                _ if !style_parts.is_empty() => {
                    tspan_parts.push(Child::Element(el("tspan", style_attr, vec![text])));
                }
                _ => tspan_parts.push(text),
            }
        }
        tspan_parts
    }

    /// `renderer.py::_build_text_runs`: spans to runs, each inline image sized
    /// for `max_width`.
    fn build_text_runs(&self, spans: &[Span], max_width: f64) -> Result<Vec<TextRun>, String> {
        spans
            .iter()
            .map(|span| {
                let image = if span.span_type == SpanType::Image {
                    Some(self.inline_image(span, max_width)?)
                } else {
                    None
                };
                Ok(TextRun {
                    text: span.text.clone(),
                    is_bold: matches!(span.span_type, SpanType::Bold | SpanType::BoldItalic),
                    is_italic: matches!(span.span_type, SpanType::Italic | SpanType::BoldItalic),
                    is_code: span.span_type == SpanType::Code,
                    is_link: span.span_type == SpanType::Link,
                    url: if span.span_type == SpanType::Link {
                        span.url.clone()
                    } else {
                        None
                    },
                    image,
                })
            })
            .collect()
    }

    /// `renderer.py::_inline_image` (`:2514`): the on-page box of an inline
    /// image. Width is the authored `{width=}`, else the authored `{height=}`
    /// times the decoded (else fallback) aspect ratio, else the decoded width,
    /// else `style.image_width`, else `available_width` — an inline image
    /// keeps its natural size rather than stretching to the column. Height is
    /// the authored `{height=}`, else width over the decoded aspect ratio, else
    /// `style.image_height`, else width over the fallback ratio. A box wider
    /// than a positive `available_width` scales down to it, aspect preserved.
    fn inline_image(&self, span: &Span, available_width: f64) -> Result<InlineImage, String> {
        let url = span.url.as_deref().expect("Span::new requires an image url");
        let actual_size = self.get_image_size(url)?;
        let mut width_is_int = false;
        let mut img_width = match (span.width, span.height, actual_size, self.style.image_width) {
            (Some(width), ..) => width,
            (None, Some(height), Some(size), _) => height * size.aspect_ratio(),
            (None, Some(height), None, _) => height * self.fallback_aspect_ratio()?,
            (None, None, Some(size), _) => {
                width_is_int = true;
                size.width
            }
            (None, None, None, Some(width)) => width,
            (None, None, None, None) => available_width,
        };
        let mut img_height = match (span.height, actual_size, self.style.image_height) {
            (Some(height), ..) => height,
            (None, Some(size), _) => img_width / size.aspect_ratio(),
            (None, None, Some(height)) => height,
            (None, None, None) => img_width / self.fallback_aspect_ratio()?,
        };
        if img_width > available_width && available_width > 0.0 {
            let scale = available_width / img_width;
            img_width *= scale;
            img_height *= scale;
            width_is_int = false;
        }
        Ok(InlineImage {
            url: url.to_string(),
            alt: span.text.clone(),
            width: img_width,
            height: img_height,
            width_is_int,
        })
    }

    /// `renderer.py::_run_pieces`: break runs into measured pieces, each
    /// carrying the separator before it. Shared by wrapping and
    /// `max_content_width`, so the width a line is asked for is the width it
    /// is laid out at.
    fn run_pieces(
        &self,
        runs: &[TextRun],
        max_width: f64,
        font_size: f64,
        font_weight: Option<&str>,
        is_heading: bool,
    ) -> Result<Vec<WrapPiece<usize>>, String> {
        // `meta` carries the originating run's index — the Rust
        // instantiation of Python's `meta: Any` field on `WrapPiece`, which
        // holds the `TextRun` itself there.
        let mut pieces: Vec<WrapPiece<usize>> = Vec::new();
        let mut separator = String::new();

        for (run_index, run) in runs.iter().enumerate() {
            let measure = |text: &str| -> Result<f64, String> {
                self.measure_text(
                    text,
                    font_size,
                    run.is_bold,
                    run.is_italic,
                    run.is_code,
                    font_weight,
                    is_heading,
                )
            };

            // An inline image is one unbreakable box in the line, like a long word.
            if let Some(image) = &run.image {
                let separator_width = if separator.is_empty() {
                    0.0
                } else {
                    measure(&separator)?
                };
                pieces.push(WrapPiece {
                    text: run.text.clone(),
                    width: image.width,
                    separator: separator.clone(),
                    separator_width,
                    meta: Some(run_index),
                    glued: separator.is_empty() && !pieces.is_empty(),
                });
                separator.clear();
                continue;
            }

            if run.is_code && !run.text.chars().all(is_py_space) && measure(&run.text)? <= max_width
            {
                let width = measure(&run.text)?;
                let separator_width = if separator.is_empty() {
                    0.0
                } else {
                    measure(&separator)?
                };
                pieces.push(WrapPiece {
                    text: run.text.clone(),
                    width,
                    separator: separator.clone(),
                    separator_width,
                    meta: Some(run_index),
                    // `renderer.py` 3029: a piece with no separator abuts the previous one.
                    glued: separator.is_empty() && !pieces.is_empty(),
                });
                separator.clear();
                continue;
            }

            for token in split_whitespace_tokens(&run.text) {
                if token.chars().all(is_py_space) {
                    separator = " ".to_string();
                    continue;
                }
                let separator_width = if separator.is_empty() {
                    0.0
                } else {
                    measure(&separator)?
                };
                // A measurement failure (no mono font configured for an
                // `is_code` run) is text-independent — it fails identically
                // for the whole token and every substring of it — so
                // checking it once here, rather than swallowing it to 0.0
                // inside the closure `split_token_precise` calls
                // per-substring, surfaces the same `Err` `?` would.
                measure(token)?;
                let measure_ref =
                    |t: &str| measure(t).expect("checked above: measure(token) already succeeded");
                for (chunk_index, chunk) in split_token_precise(token, max_width, &measure_ref)
                    .iter()
                    .enumerate()
                {
                    pieces.push(WrapPiece {
                        text: chunk.clone(),
                        width: measure(chunk)?,
                        separator: if chunk_index == 0 {
                            separator.clone()
                        } else {
                            String::new()
                        },
                        separator_width: if chunk_index == 0 {
                            separator_width
                        } else {
                            0.0
                        },
                        meta: Some(run_index),
                        // `renderer.py` 3052: only the first chunk can abut.
                        glued: chunk_index == 0 && separator.is_empty() && !pieces.is_empty(),
                    });
                    separator.clear();
                }
            }
        }
        Ok(pieces)
    }

    fn wrap_runs(
        &self,
        runs: &[TextRun],
        max_width: f64,
        font_size: f64,
        font_weight: Option<&str>,
        is_heading: bool,
    ) -> Result<Vec<Vec<TextRun>>, String> {
        if runs.is_empty() {
            return Ok(Vec::new());
        }

        let pieces = self.run_pieces(runs, max_width, font_size, font_weight, is_heading)?;
        let wrapped = wrap_measured_pieces(&pieces, max_width, self.style.avoid_runts);

        let mut lines: Vec<Vec<TextRun>> = Vec::new();
        for line in wrapped {
            let mut line_runs: Vec<TextRun> = Vec::new();
            for (index, piece) in line.iter().enumerate() {
                let run = &runs[piece.meta.unwrap()];
                let text = if index > 0 && !piece.separator.is_empty() {
                    let previous_run = &runs[line[index - 1].meta.unwrap()];
                    if previous_run.same_style(run) {
                        format!("{}{}", piece.separator, piece.text)
                    } else {
                        let separator_run = TextRun::plain(piece.separator.clone());
                        if let Some(last) = line_runs.last_mut() {
                            if last.same_style(&separator_run) {
                                *last = last.append(&piece.separator);
                            } else {
                                line_runs.push(separator_run);
                            }
                        } else {
                            line_runs.push(separator_run);
                        }
                        piece.text.clone()
                    }
                } else {
                    piece.text.clone()
                };
                if let Some(last) = line_runs.last_mut() {
                    if last.same_style(run) {
                        *last = last.append(&text);
                        continue;
                    }
                }
                line_runs.push(run.with_text(text));
            }
            if !line_runs.is_empty() {
                lines.push(line_runs);
            }
        }
        Ok(lines)
    }
}

/// Splits on runs of whitespace, keeping the whitespace runs as their own
/// tokens — mirrors Python's `re.findall(r"\S+|\s+", text)`.
fn split_whitespace_tokens(text: &str) -> Vec<&str> {
    let mut tokens = Vec::new();
    let bytes = text.as_bytes();
    let mut start = 0usize;
    let mut in_space = None;
    for (i, ch) in text.char_indices() {
        let is_space = is_py_space(ch);
        match in_space {
            None => in_space = Some(is_space),
            Some(prev) if prev != is_space => {
                tokens.push(&text[start..i]);
                start = i;
                in_space = Some(is_space);
            }
            _ => {}
        }
    }
    if start < bytes.len() {
        tokens.push(&text[start..]);
    }
    tokens
}

fn safe_href(url: &str) -> String {
    if url.starts_with('/')
        || url.starts_with('#')
        || url.starts_with('.')
        || url.starts_with('?')
        || !url.contains(':')
    {
        return url.to_string();
    }
    let scheme = url.split(':').next().unwrap().to_lowercase();
    if matches!(scheme.as_str(), "http" | "https" | "mailto") {
        url.to_string()
    } else {
        "#".to_string()
    }
}

/// Render Markdown text to nodes: the main convenience entry point. The
/// result's `height` is the extent; there is no separate measuring pass.
pub fn render(
    markdown: &str,
    width: f64,
    padding: f64,
    style: Style,
    fonts: FontSlots,
) -> Result<RenderResult, String> {
    let blocks = parse_markdown(markdown);
    let renderer = SVGRenderer::new(style, fonts)?;
    renderer.render_content(&blocks, width, padding)
}

#[cfg(test)]
mod tests {
    include!("renderer_tests.rs");
}
