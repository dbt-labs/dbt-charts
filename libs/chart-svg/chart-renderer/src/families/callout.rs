//! Callout family (`type: callout`) — a query-free markdown-driven status
//! card, and the error placard core paints for every failed chart.
//!
//! An authored callout passes `message`, `title`, `callout_style`, `width`,
//! `height` and renders both blocks as markdown. The error placard
//! additionally carries `code`, `hint` and `doc_url` and is not markdown: it arrives as
//! `resolved.placard`, its text is plain wrapped `<text>` (identifiers and
//! tracebacks are not markdown), and it records no truncation warning because
//! it has no authored YAML line to point at.
//!
//! Markdown (title + message) renders through the `mdsvg` crate — the same
//! byte-oracle port `title.rs`/`prose.rs` use for the board's own title and
//! text blocks (`libs/chart-svg/AGENTS.md`'s "`mdsvg/` crate" note). Unlike
//! those two, which receive their `Style`/`FontSlots` pre-built by the
//! Python harness (board-level font selection resolves dozens of theme
//! fields), a callout's own `_callout_md_renderer` inputs are already
//! concrete scalars sitting on `ResolvedCalloutStyle` (`title.font.*`,
//! `message.font.*`, tone-resolved colors) — no cascade left to trust
//! Python for, so this ports `_callout_md_renderer` and `get_font_path`
//! directly.
//!
//! On the family contract (`chart-renderer/AGENTS.md`): `bind` reads the
//! payload, `compose` is the one law behind both `height_for_width` and
//! `layout`, and `render` prints the nodes `layout` returns.

use std::fmt::Write as _;

use mdsvg::renderer::SVGRenderer;
use mdsvg::style::Style as MdsvgStyle;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::codes;
use crate::common::Font;
use crate::diagnostic::{warn, Diagnostic};
use crate::layout::{Insets, IntrinsicWidth, Layout, Rect};
use crate::svg::py_float_str;
use crate::Payload;
use measure::font_files::{font_path, mono_font_path};
use measure::wrap::wrap_plain;
use measure::{FontBook, Measure};
use print::print;
use scene::{Attrs, Chars, Escape, Extent, Node, TextBlock};

const MIN_WIDTH: f64 = 120.0;
const MIN_HEIGHT: f64 = 60.0;
const LINE_HEIGHT_RATIO: f64 = 1.35;
const MAX_MESSAGE_LINES: usize = 12;
const MAX_TITLE_LINES: usize = 3;
const MAX_HINT_LINES: usize = 3;
const CODE_SIZE_RATIO: f64 = 0.75;
const DOC_LINK_SIZE_RATIO: f64 = 0.85;

#[derive(Deserialize)]
struct Resolved {
    id: String,
    message: String,
    title: Option<String>,
    style: CalloutStyle,
    #[serde(default)]
    placard: Option<PlacardData>,
}

/// `render_callout_svg`'s `code`/`hint`/`doc_url`, plus the badge's own href
/// (`build_doc_url(code)`): a diagnostic with no registry entry (an engine
/// refusal) has a badge anchor but no `doc_url`, so there is no "See the
/// docs" link.
#[derive(Deserialize)]
struct PlacardData {
    code: String,
    code_url: String,
    hint: Option<String>,
    doc_url: Option<String>,
}

#[derive(Deserialize)]
struct CalloutStyle {
    preferred_width: f64,
    tone: String,
    background: String,
    border: BorderStyle,
    padding: Padding,
    section_gap: f64,
    bold_font_weight: String,
    title: ElementStyle,
    message: ElementStyle,
}

#[derive(Deserialize)]
struct ElementStyle {
    font: Font,
    y_offset: f64,
}

#[derive(Deserialize)]
struct BorderStyle {
    width: f64,
    color: String,
    radius: f64,
    #[serde(default)]
    dash_array: Option<Vec<f64>>,
    #[serde(default)]
    line_cap: Option<String>,
    #[serde(default)]
    dash_offset: Option<f64>,
}

#[derive(Deserialize)]
struct Padding {
    top: f64,
    right: f64,
    bottom: f64,
    left: f64,
}

/// A callout after `bind`: the text, style and links its payload decides,
/// before any width does.
pub struct Bound {
    id: String,
    title: Option<String>,
    message: String,
    style: CalloutStyle,
    title_color: String,
    message_color: String,
    fonts_dir: String,
    placard: Option<Placard>,
}

/// The error placard's own parts. It is plain wrapped text, not markdown, and
/// records no truncation warning: it has no authored YAML line to point at.
struct Placard {
    code: String,
    code_href: String,
    hint: Option<String>,
    doc: Option<DocLink>,
}

struct DocLink {
    href: String,
    text: String,
}

pub fn bind(payload: &Payload) -> Result<Bound, Diagnostic> {
    let r: Resolved = Deserialize::deserialize(&payload.resolved)
        .map_err(|e| format!("callout: payload.resolved: {e}"))?;
    let fonts_dir = payload
        .fonts_dir
        .clone()
        .ok_or_else(|| "callout: payload.fonts_dir is required to measure markdown".to_string())?;
    let title_color = r
        .style
        .title
        .font
        .color
        .clone()
        .ok_or("callout: title.font.color missing")?;
    let message_color = r
        .style
        .message
        .font
        .color
        .clone()
        .ok_or("callout: message.font.color missing")?;
    let placard = r
        .placard
        .map(|p| -> Result<Placard, Diagnostic> {
            let doc = match p.doc_url.as_deref() {
                Some(doc_url) => Some(DocLink {
                    href: crate::href::checked_href(doc_url)?.to_string(),
                    text: display_text::language::tr("placard.see_the_docs", None, &[])?,
                }),
                None => None,
            };
            Ok(Placard {
                code_href: crate::href::checked_href(&p.code_url)?.to_string(),
                code: p.code,
                hint: p.hint.filter(|hint| !hint.is_empty()),
                doc,
            })
        })
        .transpose()?;
    Ok(Bound {
        id: r.id,
        title: r.title.filter(|t| !t.is_empty()),
        message: r.message,
        style: r.style,
        title_color,
        message_color,
        fonts_dir,
        placard,
    })
}

/// `callout.py::_callout_md_renderer`.
fn callout_md_renderer(
    fonts_dir: &str,
    font: &Font,
    bold_font_weight: &str,
    text_color: &str,
    link_color: &str,
) -> Result<SVGRenderer, String> {
    let family = font.family_or("Inter Variable, sans-serif").to_string();
    let size = font.size_or(14.0);
    let weight = font.weight_str();
    // `None if str(coerced_fw).lower() in {"400", "normal"} else coerced_fw`
    let font_weight = if matches!(weight.to_lowercase().as_str(), "400" | "normal") {
        None
    } else {
        Some(weight)
    };
    let style = MdsvgStyle {
        font_family: family.clone(),
        base_font_size: size,
        font_weight,
        bold_font_weight: bold_font_weight.to_string(),
        line_height: LINE_HEIGHT_RATIO,
        text_color: text_color.to_string(),
        heading_color: Some(text_color.to_string()),
        link_color: link_color.to_string(),
        link_underline: true,
        code_color: text_color.to_string(),
        code_background: "rgba(0,0,0,0.07)".to_string(),
        paragraph_spacing: size * 0.5,
        list_item_spacing: 2.0,
        ..Default::default()
    };
    SVGRenderer::from_font_path(
        style,
        font_path(fonts_dir, &family),
        mono_font_path(fonts_dir),
    )
}

/// A title or message laid out: the nodes it draws at its own origin, the
/// `<style>` its markdown needs, and the height it asks for.
struct Block {
    nodes: Vec<Node>,
    styles: Vec<Node>,
    height: f64,
}

fn markdown_block(renderer: &SVGRenderer, text: &str, content_width: f64) -> Result<Block, String> {
    let doc = mdsvg::parser::parse(text);
    let r = renderer.render_content(&doc, content_width, 0.0)?;
    Ok(Block {
        nodes: mdsvg::indented_nodes(&r.elements),
        styles: mdsvg::indented_nodes(std::slice::from_ref(&r.style)),
        height: r.height,
    })
}

/// `callout.py::_plain_block`: `text` wrapped to `max_lines` as plain
/// `<text>` nodes, so `_name_` and `*args` stay literal.
fn plain_block(
    fonts_dir: &str,
    text: &str,
    content_width: f64,
    font: &Font,
    fill: &str,
    max_lines: usize,
) -> Result<Block, String> {
    let family = font.family_or("Inter Variable, sans-serif");
    let size = font.size_or(14.0);
    let lines = wrap_callout(fonts_dir, text, content_width, size, family, max_lines)?;
    let line_height = size * LINE_HEIGHT_RATIO;
    let mut nodes = Vec::new();
    for (i, line) in lines.iter().enumerate() {
        if i > 0 {
            nodes.push(Node::Chars(Chars::new("\n  ", Escape::Quick)));
        }
        nodes.push(text_line(
            Attrs::new()
                .quick("x", "0")
                .quick("y", py_float_str(size + i as f64 * line_height))
                .quick("font-size", py_float_str(size))
                .quick("font-family", family)
                .quick("fill", fill)
                .quick("text-anchor", "start"),
            line,
        ));
    }
    Ok(Block {
        nodes,
        styles: Vec::new(),
        height: lines.len() as f64 * line_height,
    })
}

fn wrap_callout(
    fonts_dir: &str,
    text: &str,
    width: f64,
    size: f64,
    family: &str,
    max_lines: usize,
) -> Result<Vec<String>, String> {
    wrap_plain(fonts_dir, text, width, size, family, max_lines).map_err(|e| format!("callout: {e}"))
}

fn truncated(chart_id: &str, field: &str, text: &str) -> Diagnostic {
    Diagnostic::new(
        &codes::WARN_CALLOUT_TEXT_TRUNCATED,
        [
            ("chart_id", json!(chart_id)),
            ("authored_field", json!(field)),
            ("authored_text", json!(text)),
        ],
    )
}

/// What a width decides, shared by `height_for_width` and `layout` so the
/// height asked for is the height drawn.
struct Composed {
    width: f64,
    content_width: f64,
    title: Option<Block>,
    message: Block,
    hint_lines: Vec<String>,
    title_size: f64,
    message_size: f64,
    max_title_height: f64,
    max_message_height: f64,
    title_height: f64,
    message_height: f64,
    badge_height: f64,
    hint_height: f64,
    natural_height: f64,
}

fn compose(bound: &Bound, width: f64) -> Result<Composed, Diagnostic> {
    let inline = &bound.style;
    let pad = &inline.padding;
    let section_gap = inline.section_gap;
    let fonts_dir = bound.fonts_dir.as_str();

    let w = width.max(MIN_WIDTH);
    let content_width = (w - pad.left - pad.right).max(40.0);

    let (title_block, message_block) = if bound.placard.is_none() {
        let title_renderer = callout_md_renderer(
            fonts_dir,
            &inline.title.font,
            &inline.bold_font_weight,
            &bound.title_color,
            &bound.title_color,
        )?;
        let message_renderer = callout_md_renderer(
            fonts_dir,
            &inline.message.font,
            &inline.bold_font_weight,
            &bound.message_color,
            &bound.title_color,
        )?;
        (
            bound
                .title
                .as_deref()
                .map(|t| markdown_block(&title_renderer, t, content_width))
                .transpose()?,
            markdown_block(&message_renderer, &bound.message, content_width)?,
        )
    } else {
        (
            bound
                .title
                .as_deref()
                .map(|t| {
                    plain_block(
                        fonts_dir,
                        t,
                        content_width,
                        &inline.title.font,
                        &bound.title_color,
                        MAX_TITLE_LINES,
                    )
                })
                .transpose()?,
            plain_block(
                fonts_dir,
                &bound.message,
                content_width,
                &inline.message.font,
                &bound.message_color,
                MAX_MESSAGE_LINES,
            )?,
        )
    };

    let title_size = inline.title.font.size_or(14.0);
    let message_size = inline.message.font.size_or(14.0);
    let message_family = inline.message.font.family_or("Inter Variable, sans-serif");
    let max_title_height = MAX_TITLE_LINES as f64 * title_size * LINE_HEIGHT_RATIO;
    let max_message_height = MAX_MESSAGE_LINES as f64 * message_size * LINE_HEIGHT_RATIO;

    let title_height = title_block
        .as_ref()
        .map_or(0.0, |b| b.height.min(max_title_height));
    let message_height = message_block.height.min(max_message_height);
    let title_gap = if title_block.is_some() {
        section_gap
    } else {
        0.0
    };

    let message_line_height = message_size * LINE_HEIGHT_RATIO;
    let hint_lines = match bound.placard.as_ref().and_then(|p| p.hint.as_deref()) {
        Some(hint) => wrap_callout(
            fonts_dir,
            hint,
            content_width,
            message_size,
            message_family,
            MAX_HINT_LINES,
        )?,
        None => Vec::new(),
    };
    let hint_height = hint_lines.len() as f64 * message_line_height;
    let has_doc = bound.placard.as_ref().is_some_and(|p| p.doc.is_some());
    let doc_height = if has_doc {
        message_size * DOC_LINK_SIZE_RATIO * LINE_HEIGHT_RATIO
    } else {
        0.0
    };
    let has_code = bound.placard.is_some();
    let badge_height = if has_code {
        title_size * CODE_SIZE_RATIO * LINE_HEIGHT_RATIO
    } else {
        0.0
    };

    let natural_height = pad.top
        + pad.bottom
        + badge_height
        + if has_code { section_gap } else { 0.0 }
        + title_height
        + title_gap
        + message_height
        + if hint_lines.is_empty() {
            0.0
        } else {
            section_gap + hint_height
        }
        + if has_doc {
            section_gap + doc_height
        } else {
            0.0
        };

    Ok(Composed {
        width: w,
        content_width,
        title: title_block,
        message: message_block,
        hint_lines,
        title_size,
        message_size,
        max_title_height,
        max_message_height,
        title_height,
        message_height,
        badge_height,
        hint_height,
        natural_height: natural_height.max(MIN_HEIGHT),
    })
}

pub fn intrinsic(bound: &Bound, _measure: &Measure) -> Result<Option<IntrinsicWidth>, Diagnostic> {
    Ok(Some(IntrinsicWidth {
        min: MIN_WIDTH,
        preferred: bound.style.preferred_width.max(MIN_WIDTH),
    }))
}

/// `max(MIN_HEIGHT, content)`: the height `render_callout_svg` draws at when no
/// slot height is given. Markdown and plain text wrap at `width`, and a font
/// file that cannot be read fails here as it does in `layout`.
pub fn height_for_width(bound: &Bound, width: f64, _measure: &Measure) -> Result<f64, Diagnostic> {
    Ok(compose(bound, width)?.natural_height)
}

/// A block's group: its translation, its clip when the block overflows its
/// cap, and its authored-kind mark.
fn block_nodes(
    content: Vec<Node>,
    (x, y): (f64, f64),
    clip: Option<(&str, f64, f64)>,
    kind: Option<&str>,
) -> Vec<Node> {
    let mut nodes = Vec::new();
    let mut attrs = Attrs::new().quick(
        "transform",
        format!("translate({}, {})", py_float_str(x), py_float_str(y)),
    );
    if let Some((id, width, height)) = clip {
        let rect = Node::Rect(
            Attrs::new()
                .quick("x", "0")
                .quick("y", "0")
                .quick("width", py_float_str(width))
                .quick("height", py_float_str(height)),
        );
        nodes.push(Node::Defs(vec![Node::ClipPath {
            attrs: Attrs::new().quick("id", id),
            children: vec![rect],
        }]));
        attrs = attrs.quick("clip-path", format!("url(#{id})"));
    }
    if let Some(kind) = kind {
        attrs = attrs.quick("data-authored-kind", kind);
    }
    nodes.push(Node::group(attrs, content));
    nodes
}

/// A line of text; `attrs` carry its position.
fn text_line(attrs: Attrs, text: &str) -> Node {
    Node::Text(TextBlock {
        attrs,
        text: Chars::new(text, Escape::Quick),
    })
}

fn link(href: &str, text: Node) -> Node {
    Node::Link {
        attrs: Attrs::new().quick("href", href).quick("target", "_blank"),
        children: vec![text],
    }
}

/// `callout.py::render_callout_svg`. `cell` is the card as drawn: its width
/// floors at `MIN_WIDTH`, its height is the caller's (`height_for_width` at
/// least).
pub fn layout(bound: &Bound, cell: Rect, _measure: &Measure) -> Result<Layout, Diagnostic> {
    draw(bound, compose(bound, cell.width)?, cell.height)
}

/// The card drawn `height` tall from its composed content.
fn draw(bound: &Bound, c: Composed, height: f64) -> Result<Layout, Diagnostic> {
    let inline = &bound.style;
    let pad = &inline.padding;
    let section_gap = inline.section_gap;
    let (w, h) = (c.width, height);
    let mut diagnostics = Vec::new();
    let mut styles = Vec::new();
    let mut body = Vec::new();
    let mut current_y = pad.top;

    if let Some(placard) = &bound.placard {
        let size = c.title_size * CODE_SIZE_RATIO;
        let text = text_line(
            Attrs::new()
                .quick("x", py_float_str(pad.left))
                .quick("y", py_float_str(current_y + size))
                .quick("font-size", py_float_str(size))
                .quick("font-weight", "normal")
                .quick("font-family", "monospace")
                .quick("fill", bound.message_color.as_str()),
            &placard.code,
        );
        body.push(link(&placard.code_href, text));
        current_y += c.badge_height + section_gap;
    }

    if let Some(block) = c.title {
        styles.extend(block.styles);
        let translate_y = current_y + inline.title.y_offset;
        let clip_id = if block.height > c.max_title_height {
            if bound.placard.is_none() {
                diagnostics.push(truncated(
                    &bound.id,
                    "title",
                    bound.title.as_deref().unwrap_or_default(),
                ));
            }
            Some(format!("title-clip-{}", callout_hash(bound, w)))
        } else {
            None
        };
        let clip = clip_id
            .as_deref()
            .map(|id| (id, c.content_width, c.title_height));
        body.extend(block_nodes(
            block.nodes,
            (pad.left, translate_y),
            clip,
            Some("title"),
        ));
        current_y += c.title_height + section_gap;
    }

    styles.extend(c.message.styles);
    let message_translate_y = current_y + inline.message.y_offset;
    let message_clip_id = if c.message.height > c.max_message_height {
        if bound.placard.is_none() {
            diagnostics.push(truncated(&bound.id, "message", &bound.message));
        }
        Some(format!("msg-clip-{}", callout_hash(bound, w)))
    } else {
        None
    };
    let message_clip = message_clip_id
        .as_deref()
        .map(|id| (id, c.content_width, c.message_height));
    body.extend(block_nodes(
        c.message.nodes,
        (pad.left, message_translate_y),
        message_clip,
        None,
    ));
    current_y += c.message_height;

    let message_family = inline.message.font.family_or("Inter Variable, sans-serif");
    let message_line_height = c.message_size * LINE_HEIGHT_RATIO;
    let hint_start_y = current_y + section_gap;
    for (i, line) in c.hint_lines.iter().enumerate() {
        body.push(text_line(
            Attrs::new()
                .quick("x", py_float_str(pad.left))
                .quick("y", py_float_str(hint_start_y + i as f64 * message_line_height))
                .quick("font-size", py_float_str(c.message_size))
                .quick("font-weight", "normal")
                .quick("font-style", "italic")
                .quick("font-family", message_family)
                .quick("fill", bound.message_color.as_str()),
            line,
        ));
    }

    if let Some(doc) = bound.placard.as_ref().and_then(|p| p.doc.as_ref()) {
        let after_hint_y = current_y
            + if c.hint_lines.is_empty() {
                0.0
            } else {
                section_gap + c.hint_height
            };
        let text = text_line(
            Attrs::new()
                .quick("x", py_float_str(pad.left))
                .quick("y", py_float_str(after_hint_y + section_gap))
                .quick("font-size", py_float_str(c.message_size * DOC_LINK_SIZE_RATIO))
                .quick("font-weight", "normal")
                .quick("font-family", message_family)
                .quick("fill", bound.title_color.as_str()),
            &doc.text,
        );
        body.push(link(&doc.href, text));
    }

    // `callout.py`'s own final return block interpolates `w`/`h`/
    // `inline.border.width`/`inline.border.radius` as bare Python floats
    // (`f'width="{w}"'`, never `format_svg_numeric`), so a whole number
    // keeps its `.0` (`rx="4.0"`, not `rx="4"`): `py_float_str`.
    let card = crate::common::border_dash_pairs(
        inline.border.dash_array.as_deref(),
        inline.border.line_cap.as_deref(),
        inline.border.dash_offset,
    )
    .into_iter()
    .fold(
        Attrs::new()
            .quick("x", "0")
            .quick("y", "0")
            .quick("width", py_float_str(w))
            .quick("height", py_float_str(h))
            .quick("fill", inline.background.as_str())
            .quick("stroke", inline.border.color.as_str())
            .quick("stroke-width", py_float_str(inline.border.width)),
        |attrs, (name, value)| attrs.quick(name, value),
    )
    .quick("rx", py_float_str(inline.border.radius));
    let mut children = vec![Node::Rect(card)];
    children.extend(styles);
    children.extend(body);

    let data_box = Rect {
        x: pad.left,
        y: pad.top,
        width: c.content_width,
        height: h - pad.top - pad.bottom,
    };
    let insets = Insets {
        top: data_box.y,
        right: w - data_box.x - data_box.width,
        bottom: pad.bottom,
        left: data_box.x,
    };
    Ok(Layout {
        data_box,
        insets,
        nodes: vec![Node::Svg {
            extent: Extent {
                width: w,
                height: h,
            },
            attrs: Attrs::new()
                .quick("xmlns", "http://www.w3.org/2000/svg")
                .quick("class", "dbt-chart-callout")
                .quick("width", py_float_str(w))
                .quick("height", py_float_str(h))
                .quick("viewBox", format!("0 0 {} {}", py_float_str(w), py_float_str(h))),
            children,
        }],
        diagnostics,
        title_pos: None,
    })
}

/// The SVG and the height it draws at when no slot height is given, which
/// core's sizing pass reads.
pub fn render_sized(payload: &Payload) -> Result<(String, f64), Diagnostic> {
    let bound = bind(payload)?;
    let composed = compose(&bound, payload.width)?;
    let natural_height = composed.natural_height;
    let laid_out = draw(&bound, composed, payload.height.max(natural_height))?;
    laid_out.diagnostics.into_iter().for_each(warn);
    Ok((print(&laid_out.nodes), natural_height))
}

/// Measurement runs through mdsvg's own font loading, so the book is unused.
pub fn render(payload: &Payload, _fonts: &FontBook) -> Result<String, Diagnostic> {
    render_sized(payload).map(|(svg, _)| svg)
}

/// Python's bare `str(x)` on a `str | float` resolved font weight — NOT
/// `Font::weight_str()`'s normalized form, which drops a float's `.0`
/// (`weight_str()` mirrors `_callout_md_renderer`'s own weight coercion,
/// a different function from the hash's raw `str()`). `callout_hash` needs
/// the unnormalized value or a themed float weight (`400.0`, not `"400"`)
/// hashes to a different id than production's.
fn hash_weight_str(font: &Font) -> String {
    match &font.weight {
        Some(Value::String(s)) => s.clone(),
        // `as_f64()` only returns `None` for a `Number` this crate cannot
        // produce (`arbitrary_precision` is off in Cargo.toml, so every
        // `Number` is backed by an i64/u64/f64 that always converts) — a
        // silent 400.0 here would be a made-up value, not a real fallback.
        Some(Value::Number(n)) => py_float_str(
            n.as_f64()
                .expect("serde_json Number without arbitrary_precision always converts"),
        ),
        // `ResolvedFontStyle.weight: str | float` is guaranteed non-null —
        // this arm is unreachable on a real payload. Empty rather than a
        // plausible-looking invented default ("400" would silently mask a
        // payload bug the hash should instead visibly disagree on).
        _ => String::new(),
    }
}

/// `callout.py`'s own clip-path id hash (lines 179-196): an md5 of every
/// resolved style input that affects clip geometry, first 8 hex chars —
/// over-keyed rather than minimal (tone/colors don't move the clip rects,
/// but hashing them only costs a different id for a same-shaped clip, never
/// a wrong one). Same input order, same `str()` formatting Python uses:
/// `py_float_str` for a bare-interpolated float, [`hash_weight_str`] for the
/// `str | float` weight fields.
fn callout_hash(bound: &Bound, w: f64) -> String {
    use md5::{Digest, Md5};
    let r = bound;
    let parts = [
        r.title.as_deref().unwrap_or(""),
        r.message.as_str(),
        r.style.tone.as_str(),
        &py_float_str(w),
        &py_float_str(r.style.padding.left),
        &py_float_str(r.style.padding.right),
        &py_float_str(r.style.padding.top),
        &py_float_str(r.style.padding.bottom),
        &py_float_str(r.style.section_gap),
        r.style.title.font.family_or(""),
        &py_float_str(r.style.title.font.size_or(0.0)),
        &hash_weight_str(&r.style.title.font),
        r.style.message.font.family_or(""),
        &py_float_str(r.style.message.font.size_or(0.0)),
        &hash_weight_str(&r.style.message.font),
        &r.title_color,
        &r.message_color,
    ];
    let digest = Md5::digest(parts.join("\x00").as_bytes());
    let mut hex = String::with_capacity(8);
    for byte in digest.iter().take(4) {
        let _ = write!(hex, "{byte:02x}");
    }
    hex
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fonts_dir() -> String {
        let manifest = env!("CARGO_MANIFEST_DIR");
        format!("{manifest}/../../../src/dbt_charts/core/render/fonts")
    }

    fn test_fonts() -> FontBook {
        FontBook::load_dir(&fonts_dir()).expect("test fonts load")
    }

    fn font_json(color: &str, size: f64, weight: &str) -> serde_json::Value {
        serde_json::json!({
            "family": "Inter Variable, sans-serif",
            "color": color,
            "size": size,
            "weight": weight,
        })
    }

    fn callout_payload(title: Option<&str>, message: &str) -> serde_json::Value {
        serde_json::json!({
            "resolved": {
                "id": "t",
                "chart_type": "callout",
                "title": title,
                "message": message,
                "style": {
                    "preferred_width": 300.0,
                    "tone": "positive",
                    "background": "#e6f4ea",
                    "border": {"width": 1.0, "color": "#0f7b3e", "radius": 6.0},
                    "padding": {"top": 16.0, "right": 16.0, "bottom": 16.0, "left": 16.0},
                    "section_gap": 8.0,
                    "bold_font_weight": "600",
                    "title": {"font": font_json("#0f7b3e", 14.0, "600"), "y_offset": 0.0},
                    "message": {"font": font_json("#1a3d29", 13.0, "400"), "y_offset": 0.0}
                }
            },
            "rows": [],
            "width": 300.0,
            "height": 100.0,
            "background": null,
            "fonts_dir": fonts_dir(),
            "padding": null
        })
    }

    #[test]
    fn renders_title_and_message_as_svg_text() {
        let json = callout_payload(Some("**Checks passed**"), "All good.");
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("callout renders");
        assert!(svg.starts_with("<svg"), "{svg}");
        assert!(svg.contains("dbt-chart-callout"), "{svg}");
        assert!(svg.contains("data-authored-kind=\"title\""), "{svg}");
        assert!(svg.contains("Checks"), "{svg}");
        assert!(svg.contains("All good."), "{svg}");
        assert!(svg.contains("fill=\"#e6f4ea\""), "{svg}");
        assert!(svg.contains("stroke=\"#0f7b3e\""), "{svg}");
    }

    #[test]
    fn no_title_skips_the_title_group() {
        let json = callout_payload(None, "Just a message.");
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("callout renders");
        assert!(!svg.contains("data-authored-kind=\"title\""), "{svg}");
        assert!(svg.contains("Just a message."), "{svg}");
    }

    #[test]
    fn height_grows_to_fit_natural_content() {
        let long = "Line one.\n\nLine two.\n\nLine three.\n\nLine four.\n\nLine five.";
        let mut json = callout_payload(Some("Title"), long);
        json["height"] = serde_json::json!(60.0);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("callout renders");
        let h: f64 = svg
            .split("height=\"")
            .nth(1)
            .unwrap()
            .split('"')
            .next()
            .unwrap()
            .parse()
            .unwrap();
        assert!(h > 60.0, "grows past the requested slot height: {h}");
    }

    #[test]
    fn card_rect_keeps_the_decimal_python_never_drops() {
        // `callout.py`'s own final return block bare-interpolates `w`/`h`/
        // `inline.border.width`/`inline.border.radius` as Python floats —
        // a whole number keeps its `.0` (`rx="4.0"`, `stroke-width="1.0"`,
        // `width="300.0"`), never the `format_svg_numeric` integer-drop
        // form (`rx="4"`). All four inputs here are whole numbers, so a
        // regression back to `num_full` would drop every one of them.
        let mut json = callout_payload(Some("Title"), "Message.");
        json["resolved"]["style"]["border"] =
            serde_json::json!({"width": 1.0, "color": "#0f7b3e", "radius": 4.0});
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("callout renders");
        assert!(svg.contains("width=\"300.0\""), "{svg}");
        assert!(svg.contains("stroke-width=\"1.0\""), "{svg}");
        assert!(svg.contains("rx=\"4.0\""), "{svg}");
    }

    /// `_callout_md_renderer` builds the renderer with `fetch_image_sizes=False`
    /// (`callout.py:123`), so `renderer.py::_render_image_block:2207-2208` never
    /// reads a source: authored dims size the image, and the block flows between
    /// paragraphs at the message's own content width (300 - 16 - 16).
    #[test]
    fn message_image_with_authored_dims_flows_between_paragraphs() {
        let message =
            "Thanks.\n\n![Acme Corp](https://example.com/acme.svg){width=48 height=24}\n\nMore.";
        let payload: Payload = serde_json::from_value(callout_payload(None, message)).unwrap();
        let svg = render(&payload, &test_fonts()).expect("callout renders");
        assert!(
            svg.contains(
                "<image x=\"0\" y=\"24.05\" width=\"48\" height=\"24\" \
                 href=\"https://example.com/acme.svg\" \
                 preserveAspectRatio=\"xMidYMid meet\"/>"
            ),
            "{svg}"
        );
        assert!(svg.contains("<title>Acme Corp</title>"), "{svg}");
    }

    /// A width with no height takes `width / image_fallback_aspect_ratio`
    /// (`renderer.py:2270-2272`, 16:9 by `style.py:252`); the alt and the
    /// href collapse space runs as `collapse_spaces` does (`renderer.py:2280,2290`).
    #[test]
    fn message_image_without_a_height_paints_at_the_fallback_ratio() {
        let message = "![A \"quoted\" <alt> & more](assets/missing.png){width=120}";
        let payload: Payload = serde_json::from_value(callout_payload(None, message)).unwrap();
        let svg = render(&payload, &test_fonts()).expect("callout renders");
        assert!(
            svg.contains(
                "<image x=\"0\" y=\"0\" width=\"120\" height=\"67.5\" \
                 href=\"assets/missing.png\" preserveAspectRatio=\"xMidYMid meet\"/>"
            ),
            "{svg}"
        );
        assert!(
            svg.contains("<title>A &quot;quoted&quot; &lt;alt&gt; &amp; more</title>"),
            "{svg}"
        );
    }

    /// The title takes the same fetch-off renderer (`callout.py:218-226`).
    #[test]
    fn title_image_is_laid_out_without_a_size_fetch() {
        let title = "Release notes\n\n![Logo](https://example.com/logo.svg){width=32 height=32}";
        let payload: Payload =
            serde_json::from_value(callout_payload(Some(title), "Shipped.")).unwrap();
        let svg = render(&payload, &test_fonts()).expect("callout renders");
        assert!(
            svg.contains(
                "<image x=\"0\" y=\"25.9\" width=\"32\" height=\"32\" \
                 href=\"https://example.com/logo.svg\" \
                 preserveAspectRatio=\"xMidYMid meet\"/>"
            ),
            "{svg}"
        );
    }

    fn placard_payload(hint: Option<&str>, doc_url: Option<&str>) -> serde_json::Value {
        let mut json = callout_payload(Some("Chart data error"), "Query returned <0 rows> for _name_.");
        json["resolved"]["placard"] = serde_json::json!({
            "code": "ERR-KPI-NO-DATA",
            "code_url": "https://docs.example.com/reference/errors/#err-kpi-no-data",
            "hint": hint,
            "doc_url": doc_url,
        });
        json
    }

    #[test]
    fn the_placard_doc_link_speaks_the_board_language() {
        let mut json = placard_payload(None, Some("https://docs.example.com/reference/errors/"));
        json["board_style"] = serde_json::json!({
            "font": "Inter", "text_mark_font": {"family": "Inter", "size": 11.0}, "secondary_color": null, "tones": null,
            "title_case": null, "continuous_height": null,
            "format_locale": crate::number_locale::en_us_wire(), "language": "de", "time_locale": crate::time_locale::en_us_wire()
        });
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = display_text::language::with_language(display_text::language::Language::new("de").unwrap(), || {
            render(&payload, &test_fonts())
        })
        .expect("placard renders");
        assert!(svg.contains(">Zur Dokumentation</text></a>"), "{svg}");
        assert!(!svg.contains("See the docs"), "{svg}");
    }

    #[test]
    fn a_card_with_no_board_names_its_language_on_the_payload() {
        let mut json = placard_payload(None, Some("https://docs.example.com/reference/errors/"));
        json["language"] = serde_json::json!("de");
        let payload: Payload = serde_json::from_value(json).unwrap();
        let (svg, _) = crate::render_sized(&payload, &test_fonts()).expect("placard renders");
        assert!(svg.contains(">Zur Dokumentation</text></a>"), "{svg}");
    }

    #[test]
    fn placard_paints_code_badge_plain_message_hint_and_doc_link() {
        let json = placard_payload(
            Some("Check the \"query\"."),
            Some("https://docs.example.com/reference/errors/#err-kpi-no-data"),
        );
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("placard renders");
        assert!(
            svg.contains(
                "<a href=\"https://docs.example.com/reference/errors/#err-kpi-no-data\" \
                 target=\"_blank\"><text x=\"16.0\" y=\"26.5\" font-size=\"10.5\" \
                 font-weight=\"normal\" font-family=\"monospace\" fill=\"#1a3d29\">\
                 ERR-KPI-NO-DATA</text></a>"
            ),
            "{svg}"
        );
        // Plain text: markdown specials stay literal, quotes use html.escape.
        assert!(svg.contains(">Query returned &lt;0 rows&gt; for _name_.</text>"), "{svg}");
        assert!(svg.contains("font-style=\"italic\""), "{svg}");
        assert!(svg.contains(">Check the &quot;query&quot;.</text>"), "{svg}");
        assert!(svg.contains(">See the docs</text></a>"), "{svg}");
        assert!(!svg.contains("<style"), "{svg}");
    }

    #[test]
    fn placard_without_hint_or_doc_url_paints_neither() {
        let payload: Payload = serde_json::from_value(placard_payload(None, None)).unwrap();
        let svg = render(&payload, &test_fonts()).expect("placard renders");
        assert!(svg.contains("ERR-KPI-NO-DATA</text></a>"), "{svg}");
        assert!(!svg.contains("font-style=\"italic\""), "{svg}");
        assert!(!svg.contains("See the docs"), "{svg}");
    }

    #[test]
    fn placard_refuses_an_unsafe_href() {
        let json = placard_payload(None, Some("javascript:alert(1)"));
        let payload: Payload = serde_json::from_value(json).unwrap();
        let err = render(&payload, &test_fonts()).expect_err("refuses");
        assert!(err.contains("ERR-LINK-SCHEME-UNSAFE-AT-RENDER"), "{err}");
    }

    #[test]
    fn placard_natural_height_counts_every_section() {
        let bare: Payload = serde_json::from_value(placard_payload(None, None)).unwrap();
        let full: Payload = serde_json::from_value(placard_payload(
            Some("Fix it."),
            Some("https://docs.example.com/x"),
        ))
        .unwrap();
        let (_, bare_height) = render_sized(&bare).unwrap();
        let (_, full_height) = render_sized(&full).unwrap();
        // hint: gap 8 + one line 13*1.35; doc link: gap 8 + 13*0.85*1.35.
        let expected = 8.0 + 13.0 * 1.35 + 8.0 + 13.0 * 0.85 * 1.35;
        assert!((full_height - bare_height - expected).abs() < 1e-9, "{bare_height} {full_height}");
    }

    #[test]
    fn natural_height_is_independent_of_the_slot_height() {
        let mut json = callout_payload(Some("Title"), "Message.");
        json["height"] = serde_json::json!(0.0);
        let small: Payload = Deserialize::deserialize(&json).unwrap();
        json["height"] = serde_json::json!(500.0);
        let tall: Payload = serde_json::from_value(json).unwrap();
        let (small_svg, small_height) = render_sized(&small).unwrap();
        let (tall_svg, tall_height) = render_sized(&tall).unwrap();
        assert_eq!(small_height, tall_height);
        assert!(small_height >= MIN_HEIGHT);
        assert!(small_svg.contains(&format!("height=\"{}\"", py_float_str(small_height))));
        assert!(tall_svg.contains("height=\"500.0\""), "{tall_svg}");
    }

    #[test]
    fn placard_overflow_records_no_truncation_warning() {
        let long = "word ".repeat(400);
        // At size 12 the 12-line cap (12 * 12 * 1.35) is exceeded by a capped
        // block's float height, so the clip branch runs and only the placard
        // guard stops the warning; at size 13 it never runs.
        let mut json = placard_payload(None, None);
        json["resolved"]["message"] = serde_json::json!(long);
        json["resolved"]["style"]["message"]["font"]["size"] = serde_json::json!(12.0);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let (rendered, warnings) = crate::diagnostic::collect(|| render(&payload, &test_fonts()));
        let svg = rendered.expect("placard renders");
        assert!(svg.contains("msg-clip-"), "{svg}");
        assert!(warnings.is_empty(), "{warnings:?}");

        let json = callout_payload(None, &long);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let (rendered, warnings) = crate::diagnostic::collect(|| render(&payload, &test_fonts()));
        rendered.expect("callout renders");
        assert_eq!(warnings.len(), 1, "{warnings:?}");
    }

    #[test]
    fn missing_fonts_dir_refuses() {
        let mut json = callout_payload(Some("Title"), "Message.");
        json["fonts_dir"] = serde_json::Value::Null;
        let payload: Payload = serde_json::from_value(json).unwrap();
        let err = render(&payload, &test_fonts()).expect_err("refuses");
        assert!(err.contains("fonts_dir"), "{err}");
    }

    #[test]
    fn a_message_taller_than_its_cap_warns_with_the_authored_text() {
        let message = "line\n\n".repeat(30);
        let payload: Payload = serde_json::from_value(callout_payload(None, &message)).unwrap();
        let (svg, warnings) = crate::diagnostic::collect(|| render(&payload, &test_fonts()));
        svg.expect("renders");
        assert_eq!(warnings.len(), 1);
        assert_eq!(warnings[0].code, "WARN-CALLOUT-TEXT-TRUNCATED");
        assert_eq!(
            serde_json::to_value(&warnings[0].params).unwrap(),
            serde_json::json!({"chart_id": "t", "authored_field": "message", "authored_text": message})
        );
    }

    #[test]
    fn a_message_within_its_cap_warns_nothing() {
        let payload: Payload = serde_json::from_value(callout_payload(None, "Shipped.")).unwrap();
        let (svg, warnings) = crate::diagnostic::collect(|| render(&payload, &test_fonts()));
        svg.expect("renders");
        assert!(warnings.is_empty());
    }

    /// mdsvg escapes `\t`, `\n` and `\r` as character references; the card
    /// keeps them rather than printing the raw characters.
    #[test]
    fn whitespace_in_markdown_text_prints_as_character_references() {
        let message = "a\tb\n\n```\nx\ty\n```";
        let payload: Payload = serde_json::from_value(callout_payload(None, message)).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(svg.contains(">x&#9;y</text>"), "{svg}");
        assert!(!svg.contains('\t'), "{svg:?}");
    }

    /// mdsvg closes only `image`, `rect`, `line` and `circle` as `<x/>`; an
    /// empty table cell stays a `<text></text>` pair.
    #[test]
    fn an_empty_markdown_element_keeps_the_close_tag_mdsvg_wrote() {
        let payload: Payload =
            serde_json::from_value(callout_payload(None, "| h |\n|---|\n|   |")).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(svg.contains("text-anchor=\"start\"></text>"), "{svg}");
    }

    fn bound_of(json: serde_json::Value) -> Bound {
        bind(&serde_json::from_value(json).unwrap()).expect("binds")
    }

    fn contract_cases() -> Vec<serde_json::Value> {
        let mut clipped = callout_payload(Some("Title"), &"line\n\n".repeat(30));
        clipped["resolved"]["style"]["message"]["font"]["size"] = serde_json::json!(12.0);
        vec![
            callout_payload(Some("**Checks passed**"), "All good."),
            callout_payload(None, "Just a message."),
            clipped,
            placard_payload(
                Some("Fix it."),
                Some("https://docs.example.com/reference/errors/"),
            ),
        ]
    }

    #[test]
    fn height_for_width_is_the_height_layout_draws_into_its_own_answer() {
        let fonts = test_fonts();
        let measure = Measure { fonts: &fonts };
        for json in contract_cases() {
            let bound = bound_of(json);
            for width in [120.0, 300.0, 480.0] {
                let natural = height_for_width(&bound, width, &measure).unwrap();
                let cell = Rect { x: 0.0, y: 0.0, width, height: natural };
                let laid_out = layout(&bound, cell, &measure).unwrap();
                let [Node::Svg { extent, .. }] = laid_out.nodes.as_slice() else {
                    panic!("a card is one root");
                };
                assert_eq!((extent.width, extent.height), (width, natural));
                let Insets { top, bottom, .. } = laid_out.insets;
                assert_eq!(top + laid_out.data_box.height + bottom, natural);
            }
        }
    }

    #[test]
    fn layout_draws_the_cell_height_it_is_given() {
        let fonts = test_fonts();
        let measure = Measure { fonts: &fonts };
        let bound = bound_of(callout_payload(Some("Title"), "Message."));
        let cell = Rect { x: 0.0, y: 0.0, width: 300.0, height: 500.0 };
        let laid_out = layout(&bound, cell, &measure).unwrap();
        assert!(matches!(laid_out.nodes.as_slice(), [Node::Svg { extent, .. }] if extent.height == 500.0));
    }

    #[test]
    fn intrinsic_min_never_exceeds_preferred() {
        let fonts = test_fonts();
        let measure = Measure { fonts: &fonts };
        for json in contract_cases() {
            let IntrinsicWidth { min, preferred } =
                intrinsic(&bound_of(json), &measure).unwrap().unwrap();
            assert!(min <= preferred, "{min} {preferred}");
        }
        let mut narrow = callout_payload(None, "Message.");
        narrow["resolved"]["style"]["preferred_width"] = serde_json::json!(80.0);
        let width = intrinsic(&bound_of(narrow), &measure).unwrap().unwrap();
        assert_eq!((width.min, width.preferred), (MIN_WIDTH, MIN_WIDTH));
    }

    #[test]
    fn layout_returns_the_truncation_warning_it_decided() {
        let fonts = test_fonts();
        let measure = Measure { fonts: &fonts };
        let message = "line\n\n".repeat(30);
        let bound = bound_of(callout_payload(None, &message));
        let cell = Rect { x: 0.0, y: 0.0, width: 300.0, height: 100.0 };
        let (laid_out, sunk) = crate::diagnostic::collect(|| layout(&bound, cell, &measure));
        let laid_out = laid_out.unwrap();
        assert!(sunk.is_empty(), "layout hands its WARNs back, it does not sink them");
        assert_eq!(laid_out.diagnostics.len(), 1);
        assert_eq!(laid_out.diagnostics[0].code, "WARN-CALLOUT-TEXT-TRUNCATED");
    }
}
