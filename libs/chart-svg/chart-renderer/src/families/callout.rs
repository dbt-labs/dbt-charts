//! Callout family (`type: callout`) — a query-free markdown-driven status
//! card, and the error placard core paints for every failed chart.
//!
//! An authored callout passes `message`, `title`, `callout_style`, `width`,
//! `height` and renders both blocks as markdown. The error placard
//! additionally carries `code`, `code_url` and `hint` as `resolved.placard`
//! and is drawn by `placard.rs`, not as markdown.
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

mod placard;

use mdsvg::renderer::SVGRenderer;
use mdsvg::style::Style as MdsvgStyle;
use serde::Deserialize;
use serde_json::{json, Value};

use diagnostic::codes;
use crate::common::Font;
use diagnostic::{warn, Diagnostic};
use crate::layout::{Insets, IntrinsicWidth, Layout, Rect};
use crate::Payload;
use measure::font_files::{font_path, mono_font_path};
use measure::fonts::{FontSlot, FontSlots};
use measure::wrap::wrap_plain;
use measure::{FontBook, Measure};
use print::print;
use scene::{Attrs, Chars, Escape, Extent, Node, Role, Run, Shape};

const MIN_WIDTH: f64 = 120.0;
const MIN_HEIGHT: f64 = 60.0;
const LINE_HEIGHT_RATIO: f64 = 1.35;
const MAX_MESSAGE_LINES: usize = 12;
const MAX_TITLE_LINES: usize = 3;

#[derive(Deserialize)]
struct Resolved {
    id: String,
    message: String,
    title: Option<String>,
    style: CalloutStyle,
    #[serde(default)]
    placard: Option<placard::Data>,
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
    /// The theme's code font: markdown code and the error tile's code
    /// headline paint in it.
    code_family: String,
    title: ElementStyle,
    message: ElementStyle,
    /// The color of the error tile's stripes; only an error tile carries one.
    #[serde(default)]
    stripe_color: Option<String>,
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
    placard: Option<placard::Placard>,
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
        .map(|data| placard::bind(data, r.style.stripe_color.as_deref(), &r.style.code_family))
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

fn callout_md_renderer(
    fonts_dir: &str,
    font: &Font,
    bold_font_weight: &str,
    code_family: &str,
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
        code_font_family: code_family.to_string(),
        code_color: text_color.to_string(),
        code_background: "rgba(0,0,0,0.07)".to_string(),
        paragraph_spacing: size * 0.5,
        list_item_spacing: 2.0,
        ..Default::default()
    };
    // Bold paints from the same variable file at `bold_font_weight`; without
    // that slot mdsvg estimates bold by a width ratio and every run after it,
    // inline-code chips included, lands to the right of its painted glyphs.
    let regular = FontSlot::new(font_path(fonts_dir, &family));
    let bold = FontSlot {
        weight: measure::fonts::css_weight_axis(bold_font_weight)?,
        ..regular.clone()
    };
    SVGRenderer::new(
        style,
        FontSlots {
            bold: Some(bold),
            mono: Some(FontSlot::new(mono_font_path(fonts_dir))),
            ..FontSlots::new(regular)
        },
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
    max_title_height: f64,
    max_message_height: f64,
    title_height: f64,
    message_height: f64,
    natural_height: f64,
}

fn compose(bound: &Bound, width: f64) -> Result<Composed, Diagnostic> {
    let inline = &bound.style;
    let pad = &inline.padding;
    let section_gap = inline.section_gap;
    let fonts_dir = bound.fonts_dir.as_str();

    let w = width.max(MIN_WIDTH);
    let content_width = (w - pad.left - pad.right).max(40.0);

    let title_renderer = callout_md_renderer(
        fonts_dir,
        &inline.title.font,
        &inline.bold_font_weight,
        &inline.code_family,
        &bound.title_color,
        &bound.title_color,
    )?;
    let message_renderer = callout_md_renderer(
        fonts_dir,
        &inline.message.font,
        &inline.bold_font_weight,
        &inline.code_family,
        &bound.message_color,
        &bound.title_color,
    )?;
    let title_block = bound
        .title
        .as_deref()
        .map(|t| markdown_block(&title_renderer, t, content_width))
        .transpose()?;
    let message_block = markdown_block(&message_renderer, &bound.message, content_width)?;

    let title_size = inline.title.font.size_or(14.0);
    let message_size = inline.message.font.size_or(14.0);
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

    let natural_height = pad.top + pad.bottom + title_height + title_gap + message_height;

    Ok(Composed {
        width: w,
        content_width,
        title: title_block,
        message: message_block,
        max_title_height,
        max_message_height,
        title_height,
        message_height,
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
    if bound.placard.is_some() {
        return Ok(placard::compose(bound, width)?.natural_height);
    }
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
    let mut attrs = Attrs::new();
    if let Some((id, width, height)) = clip {
        let rect = Node::rect(Role::Chrome, (0.0, 0.0, width, height), Attrs::new());
        let clip_path = Node::new(Role::Chrome, Attrs::new().quick("id", id), Shape::ClipPath(vec![rect]));
        nodes.push(Node::new(Role::Chrome, Attrs::new(), Shape::Defs(vec![clip_path])));
        attrs = attrs.quick("clip-path", format!("url(#{id})"));
    }
    if let Some(kind) = kind {
        attrs = attrs.quick("data-authored-kind", kind);
    }
    nodes.push(Node::group(Role::Chrome, attrs, content).translate(x, y));
    nodes
}

/// A line of text at `at`.
fn text_line(role: Role, at: (f64, f64), attrs: Attrs, text: &str) -> Node {
    let runs = vec![Run::Chars(Chars::new(text, Escape::Quick))];
    Node::new(role, attrs, Shape::Text { at, bounds: None, runs })
}

fn link(href: &str, text: Node) -> Node {
    let attrs = Attrs::new().quick("href", href).quick("target", "_blank");
    Node::new(Role::Chrome, attrs, Shape::Link(vec![text]))
}

/// `callout.py::render_callout_svg`. `cell` is the card as drawn: its width
/// floors at `MIN_WIDTH`, its height is the caller's (`height_for_width` at
/// least).
pub fn layout(bound: &Bound, cell: Rect, _measure: &Measure) -> Result<Layout, Diagnostic> {
    if bound.placard.is_some() {
        return placard::draw(bound, placard::compose(bound, cell.width)?, cell.height);
    }
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

    if let Some(block) = c.title {
        styles.extend(block.styles);
        let translate_y = current_y + inline.title.y_offset;
        let clip_id = if block.height > c.max_title_height {
            diagnostics.push(truncated(
                &bound.id,
                "title",
                bound.title.as_deref().unwrap_or_default(),
            ));
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
        diagnostics.push(truncated(&bound.id, "message", &bound.message));
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

    let mut children = vec![card_rect(inline, w, 0.0, h)];
    children.extend(styles);
    children.extend(body);

    let data_box = Rect {
        x: pad.left,
        y: pad.top,
        width: c.content_width,
        height: h - pad.top - pad.bottom,
    };
    Ok(Layout {
        insets: insets_of(&data_box, w, pad.bottom),
        data_box,
        nodes: vec![card_svg(w, h, children)],
        diagnostics,
        title_pos: None,
    })
}

/// The card's rounded rectangle from `top` to the slot's bottom.
fn card_rect(inline: &CalloutStyle, w: f64, top: f64, h: f64) -> Node {
    let attrs = crate::common::border_dash_pairs(
        inline.border.dash_array.as_deref(),
        inline.border.line_cap.as_deref(),
        inline.border.dash_offset,
    )
    .into_iter()
    .fold(
        Attrs::new()
            .quick("fill", inline.background.as_str())
            .quick("stroke", inline.border.color.as_str())
            .num("stroke-width", inline.border.width),
        |attrs, (name, value)| attrs.quick(name, value),
    );
    let rect = Shape::Rect { x: 0.0, y: top, w, h: h - top, rx: inline.border.radius };
    Node::new(Role::Chrome, attrs, rect)
}

fn insets_of(data_box: &Rect, w: f64, bottom: f64) -> Insets {
    Insets {
        top: data_box.y,
        right: w - data_box.x - data_box.width,
        bottom,
        left: data_box.x,
    }
}

fn card_svg(w: f64, h: f64, children: Vec<Node>) -> Node {
    let extent = Extent { width: w, height: h };
    Node::new(Role::Chrome, Attrs::new().quick("class", "dbt-chart-callout"), Shape::Svg { extent, children })
}

/// The SVG and the height it draws at when no slot height is given, which
/// core's sizing pass reads.
pub fn render_sized(payload: &Payload) -> Result<(String, f64), Diagnostic> {
    let bound = bind(payload)?;
    if bound.placard.is_some() {
        let composed = placard::compose(&bound, payload.width)?;
        let natural_height = composed.natural_height;
        let laid_out = placard::draw(&bound, composed, payload.height.max(natural_height))?;
        return Ok((print(&laid_out.nodes), natural_height));
    }
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

/// A resolved `str | float` font weight as the hash reads it: a number as
/// `print` writes it, a string as authored. Unlike `Font::weight_str()` it
/// invents no default, so a missing weight keys a different id.
fn hash_weight_str(font: &Font) -> String {
    match &font.weight {
        Some(Value::String(s)) => s.clone(),
        // `as_f64()` only returns `None` for a `Number` this crate cannot
        // produce (`arbitrary_precision` is off in Cargo.toml, so every
        // `Number` is backed by an i64/u64/f64 that always converts) — a
        // silent 400.0 here would be a made-up value, not a real fallback.
        Some(Value::Number(n)) => print::number(
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

/// The clip and stripe ids' hash: an md5 of every resolved style input that
/// affects clip geometry, first 8 hex chars, over-keyed rather than minimal
/// (tone and colors don't move the clip rects, but hashing them only costs a
/// different id for a same-shaped clip, never a wrong one). Each number
/// is hashed as `print` writes a number, never as a formatted coordinate.
fn callout_hash(bound: &Bound, w: f64) -> String {
    use md5::{Digest, Md5};
    let r = bound;
    let parts = [
        r.title.as_deref().unwrap_or(""),
        r.message.as_str(),
        r.style.tone.as_str(),
        &print::number(w),
        &print::number(r.style.padding.left),
        &print::number(r.style.padding.right),
        &print::number(r.style.padding.top),
        &print::number(r.style.padding.bottom),
        &print::number(r.style.section_gap),
        r.style.title.font.family_or(""),
        &print::number(r.style.title.font.size_or(0.0)),
        &hash_weight_str(&r.style.title.font),
        r.style.message.font.family_or(""),
        &print::number(r.style.message.font.size_or(0.0)),
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
                    "code_family": "'Source Code Pro', monospace",
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
    fn a_code_chip_starts_where_the_painted_bold_before_it_ends() {
        let mut json = callout_payload(None, "**Owner role:** `role_group` rest");
        json["width"] = serde_json::json!(600.0);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("callout renders");
        let chip = svg.split("<rect").find(|r| r.contains("rgba(0,0,0,0.07)")).expect("a chip");
        let x: f64 = chip.split("x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
        let inter = format!("{}/InterVariable.ttf", fonts_dir());
        let bold = measure::fonts::cached_measurer(&inter, 0, Some(600.0));
        let regular = measure::fonts::cached_measurer(&inter, 0, None);
        let chip_left = bold.measure("Owner role:", 13.0) + regular.measure(" ", 13.0);
        assert!((x - chip_left).abs() < 0.01, "chip x {x}, preceding text ends at {chip_left}");
    }

    #[test]
    fn markdown_code_paints_in_the_theme_code_font_it_is_measured_in() {
        let json = callout_payload(None, "use `x`");
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("callout renders");
        let code_rule = svg.lines().find(|l| l.contains("-code {")).expect("a code rule");
        assert!(code_rule.contains("Source Code Pro"), "{code_rule}");
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
    fn card_rect_prints_whole_numbers_bare() {
        let mut json = callout_payload(Some("Title"), "Message.");
        json["resolved"]["style"]["border"] =
            serde_json::json!({"width": 1.0, "color": "#0f7b3e", "radius": 4.0});
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("callout renders");
        assert!(svg.contains("width=\"300\""), "{svg}");
        assert!(svg.contains("stroke-width=\"1\""), "{svg}");
        assert!(svg.contains("rx=\"4\""), "{svg}");
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

    const CODE_URL: &str = "https://docs.example.com/reference/errors/#err-kpi-no-data";

    fn placard_payload(hint: Option<&str>, code_url: &str) -> serde_json::Value {
        let mut json = callout_payload(None, "Query returned <0 rows> for _name_.");
        json["resolved"]["placard"] = serde_json::json!({
            "code": "ERR-KPI-NO-DATA",
            "code_url": code_url,
            "hint": hint,
            "chart_title": null,
            "chart_title_font": font_json("#222222", 16.0, "600"),
            "chart_title_baseline": 0.0,
            "tile_top": 0.0,
            "content_left": 16.0,
        });
        json["resolved"]["style"]["stripe_color"] = serde_json::json!("#e0e0e0");
        json
    }

    fn placard_svg(json: serde_json::Value) -> String {
        let payload: Payload = serde_json::from_value(json).unwrap();
        render(&payload, &test_fonts()).expect("placard renders")
    }

    #[test]
    fn the_placard_link_speaks_the_board_language() {
        let mut json = placard_payload(None, CODE_URL);
        json["board_style"] = serde_json::json!({
            "font": "Inter", "text_mark_font": {"family": "Inter", "size": 11.0}, "secondary_color": null, "tones": null,
            "title_case": null, "continuous_height": null,
            "format_locale": ::bind::formats::locale::en_us_wire(), "language": "de", "time_locale": ::bind::time_locale::en_us_wire()
        });
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = display_text::language::with_language(display_text::language::Language::new("de").unwrap(), || {
            render(&payload, &test_fonts())
        })
        .expect("placard renders");
        assert!(svg.contains(">Über diesen Fehler</text></a>"), "{svg}");
        assert!(!svg.contains("About this error"), "{svg}");
    }

    #[test]
    fn a_card_with_no_board_names_its_language_on_the_payload() {
        let mut json = placard_payload(None, CODE_URL);
        json["language"] = serde_json::json!("de");
        let payload: Payload = serde_json::from_value(json).unwrap();
        let (svg, _) = crate::render_sized(&payload, &test_fonts()).expect("placard renders");
        assert!(svg.contains(">Über diesen Fehler</text></a>"), "{svg}");
    }

    #[test]
    fn placard_paints_headline_message_hint_and_a_closing_link() {
        let svg = placard_svg(placard_payload(Some("Check the \"query\"."), CODE_URL));
        let at = |needle: &str| svg.find(needle).unwrap_or_else(|| panic!("{needle}: {svg}"));
        // Headline code, message, hint, then the link last.
        assert!(
            at("ERR-KPI-NO-DATA") < at("Query returned")
                && at("Query returned") < at("Check the")
                && at("Check the") < at("About this error"),
            "{svg}"
        );
        // The code is mono body ink, neither a link nor underlined.
        assert!(
            svg.contains("font-family=\"&apos;Source Code Pro&apos;, monospace\" fill=\"#1a3d29\">ERR-KPI-NO-DATA</text>"),
            "{svg}"
        );
        assert!(!svg.contains("ERR-KPI-NO-DATA</text></a>"), "{svg}");
        // The one link is underlined ink and ends on the code's own anchor.
        assert_eq!(svg.matches("<a ").count(), 1, "{svg}");
        assert!(
            svg.contains(&format!(
                "<a href=\"{CODE_URL}\" target=\"_blank\"><text x=\"28\""
            )) && svg.contains("text-decoration=\"underline\" fill=\"#1a3d29\">About this error</text></a>"),
            "{svg}"
        );
        // Plain text: markdown specials stay literal, quotes use html.escape.
        assert!(svg.contains(">Query returned &lt;0 rows&gt; for _name_.</text>"), "{svg}");
        assert!(svg.contains(">Check the &quot;query&quot;.</text>"), "{svg}");
        assert!(!svg.contains("font-style=\"italic\"") && !svg.contains("<style"), "{svg}");
    }

    #[test]
    fn placard_draws_the_warning_glyph_from_primitives() {
        let svg = placard_svg(placard_payload(None, CODE_URL));
        assert!(svg.contains("<path d=\"M6.5,0.9L12.3,11.1L0.7,11.1Z\" fill=\"none\""), "{svg}");
        assert!(svg.contains("<circle"), "{svg}");
        assert!(!svg.contains('\u{26a0}'), "{svg}");
    }

    #[test]
    fn placard_stripes_are_a_static_pattern_the_tile_fills_with() {
        let svg = placard_svg(placard_payload(None, CODE_URL));
        assert!(svg.contains("<pattern width=\"8\" height=\"8\" patternTransform=\"rotate(45)\" id=\"err-stripes-"), "{svg}");
        assert!(svg.contains("patternTransform=\"rotate(45)\""), "{svg}");
        assert!(svg.contains("fill=\"#e0e0e0\""), "{svg}");
        assert!(svg.contains("fill=\"url(#err-stripes-"), "{svg}");
    }

    /// The plate's `(x, y, width, height)`: the one rect with the plate's radius.
    fn plate_of(svg: &str) -> (f64, f64, f64, f64) {
        let rect = svg
            .split("<rect ")
            .map(|r| r.split("/>").next().unwrap_or(r))
            .find(|r| r.contains("rx=\"3\""))
            .unwrap_or_else(|| panic!("plate: {svg}"));
        let attr = |name: &str| -> f64 {
            rect.split(&format!(" {name}=\"")).nth(1).or_else(|| rect.strip_prefix(&format!("{name}=\"")))
                .and_then(|r| r.split('"').next())
                .and_then(|v| v.parse().ok())
                .unwrap_or_else(|| panic!("{name}: {rect}"))
        };
        (attr("x"), attr("y"), attr("width"), attr("height"))
    }

    #[test]
    fn placard_plate_sits_inset_in_the_panel_and_its_text_a_padding_further() {
        let svg = placard_svg(placard_payload(None, CODE_URL));
        let (x, y, _, _) = plate_of(&svg);
        assert!(x > 0.0 && y > 0.0, "stripes show left of and above the plate: {svg}");
        // The glyph starts a plate padding in from the plate's edge.
        assert!(svg.contains(&format!("translate({},", print::geometry(x + super::placard::PLATE_PAD_X))), "{svg}");
        assert!(svg.contains("rx=\"3\""), "{svg}");
    }

    #[test]
    fn a_short_slot_squeezes_the_inset_before_it_clips_anything() {
        let tall = placard_svg(placard_payload(None, CODE_URL));
        let mut json = placard_payload(None, CODE_URL);
        json["height"] = serde_json::json!(0.0);
        let bound = bind(&serde_json::from_value::<Payload>(json).unwrap()).unwrap();
        let measure_fonts = test_fonts();
        let measure = Measure { fonts: &measure_fonts };
        let natural = height_for_width(&bound, 300.0, &measure).unwrap();
        let cell = Rect { x: 0.0, y: 0.0, width: 300.0, height: natural - 14.0 };
        let laid_out = layout(&bound, cell, &measure).unwrap();
        let short = print(&laid_out.nodes);
        assert!(plate_of(&short).1 < plate_of(&tall).1, "{short}");
        assert!(plate_of(&short).1 >= 12.0, "{short}");
    }

    #[test]
    fn a_wide_placard_caps_its_text_block_at_a_readable_column() {
        let mut json = placard_payload(None, CODE_URL);
        json["resolved"]["message"] = serde_json::json!("word ".repeat(200));
        json["width"] = serde_json::json!(1200.0);
        let svg = placard_svg(json);
        let (_, _, plate, _) = plate_of(&svg);
        // 40 ch of 13px body text (a "0" is about 0.62em) is well under the tile's width.
        assert!(plate < 40.0 * 13.0 * 0.7 + 2.0 * 12.0, "{plate}");
        assert!(plate > 150.0, "{plate}");
    }

    #[test]
    fn a_narrow_placard_wraps_the_code_at_its_hyphens_and_never_truncates_it() {
        let mut json = placard_payload(None, CODE_URL);
        json["resolved"]["placard"]["code"] = serde_json::json!("ERR-CHART-COLUMN-NOT-IN-RESULT");
        json["width"] = serde_json::json!(150.0);
        let svg = placard_svg(json);
        let code_lines: Vec<&str> = svg
            .split("font-family=\"&apos;Source Code Pro&apos;, monospace\"")
            .skip(1)
            .map(|rest| rest.split('>').nth(1).unwrap().split('<').next().unwrap())
            .collect();
        assert!(code_lines.len() > 1, "{svg}");
        assert!(code_lines[..code_lines.len() - 1].iter().all(|l| l.ends_with('-')), "{code_lines:?}");
        assert_eq!(code_lines.concat(), "ERR-CHART-COLUMN-NOT-IN-RESULT");
    }

    #[test]
    fn placard_keeps_the_chart_title_above_a_tile_that_moves_down_with_it() {
        let mut json = placard_payload(None, CODE_URL);
        json["resolved"]["placard"]["chart_title"] = serde_json::json!("revenue by region");
        json["resolved"]["placard"]["chart_title_baseline"] = serde_json::json!(30.0);
        json["resolved"]["placard"]["tile_top"] = serde_json::json!(40.0);
        json["resolved"]["placard"]["chart_title_font"]["case"] = serde_json::json!("title");
        let titled: Payload = serde_json::from_value(json).unwrap();
        let bare: Payload = serde_json::from_value(placard_payload(None, CODE_URL)).unwrap();
        let (svg, titled_height) = render_sized(&titled).unwrap();
        let (_, bare_height) = render_sized(&bare).unwrap();
        // The tile starts where the band says, and the title is cased as a healthy one is.
        assert!((titled_height - bare_height - 40.0).abs() < 1e-9);
        assert!(svg.contains("y=\"40\""), "{svg}");
        assert!(svg.contains("x=\"16\" y=\"30\"") && !svg.contains("revenue by region"), "{svg}");
        assert!(svg.find("Revenue by Region") < svg.find("ERR-KPI-NO-DATA"), "{svg}");
    }

    #[test]
    fn placard_without_a_hint_paints_none() {
        let svg = placard_svg(placard_payload(None, CODE_URL));
        assert!(!svg.contains("Fix it"), "{svg}");
    }

    #[test]
    fn placard_refuses_an_unsafe_href() {
        let json = placard_payload(None, "javascript:alert(1)");
        let payload: Payload = serde_json::from_value(json).unwrap();
        let err = render(&payload, &test_fonts()).expect_err("refuses");
        assert!(err.contains("ERR-LINK-SCHEME-UNSAFE-AT-RENDER"), "{err}");
    }

    #[test]
    fn placard_natural_height_counts_the_hint() {
        let bare: Payload = serde_json::from_value(placard_payload(None, CODE_URL)).unwrap();
        let full: Payload = serde_json::from_value(placard_payload(Some("Fix it."), CODE_URL)).unwrap();
        let (_, bare_height) = render_sized(&bare).unwrap();
        let (_, full_height) = render_sized(&full).unwrap();
        // One more line of body text plus its gap.
        assert!((full_height - bare_height - (4.0 + 13.0 * 1.35)).abs() < 1e-9, "{bare_height} {full_height}");
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
        assert!(small_svg.contains(&format!("height=\"{}\"", print::number(small_height))));
        assert!(tall_svg.contains("height=\"500\""), "{tall_svg}");
    }

    #[test]
    fn placard_overflow_records_no_truncation_warning() {
        let long = "word ".repeat(400);
        let mut json = placard_payload(None, CODE_URL);
        json["resolved"]["message"] = serde_json::json!(long);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let (rendered, warnings) = diagnostic::collect(|| render(&payload, &test_fonts()));
        rendered.expect("placard renders");
        assert!(warnings.is_empty(), "{warnings:?}");

        let json = callout_payload(None, &long);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let (rendered, warnings) = diagnostic::collect(|| render(&payload, &test_fonts()));
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
        let (svg, warnings) = diagnostic::collect(|| render(&payload, &test_fonts()));
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
        let (svg, warnings) = diagnostic::collect(|| render(&payload, &test_fonts()));
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
            placard_payload(Some("Fix it."), CODE_URL),
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
                let Some(extent) = scene::root_extent(&laid_out.nodes) else {
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
        assert!(scene::root_extent(&laid_out.nodes).is_some_and(|extent| extent.height == 500.0));
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
        let (laid_out, sunk) = diagnostic::collect(|| layout(&bound, cell, &measure));
        let laid_out = laid_out.unwrap();
        assert!(sunk.is_empty(), "layout hands its WARNs back, it does not sink them");
        assert_eq!(laid_out.diagnostics.len(), 1);
        assert_eq!(laid_out.diagnostics[0].code, "WARN-CALLOUT-TEXT-TRUNCATED");
    }
}
