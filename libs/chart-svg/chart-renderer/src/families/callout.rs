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

use std::fmt::Write as _;

use mdsvg::fonts::{cached_measurer, wrap_text_precise};
use mdsvg::renderer::SVGRenderer;
use mdsvg::style::Style as MdsvgStyle;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::codes;
use crate::common::Font;
use crate::diagnostic::{warn, Diagnostic};
use crate::svg::py_float_str;
use crate::text::FontBook;
use crate::xml::{Attrs, Xml};
use crate::Payload;

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
    placard: Option<Placard>,
}

/// `render_callout_svg`'s `code`/`hint`/`doc_url`, plus the badge's own href
/// (`build_doc_url(code)`): a diagnostic with no registry entry (an engine
/// refusal) has a badge anchor but no `doc_url`, so there is no "See the
/// docs" link.
#[derive(Deserialize)]
struct Placard {
    code: String,
    code_url: String,
    hint: Option<String>,
    doc_url: Option<String>,
}

#[derive(Deserialize)]
struct CalloutStyle {
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

/// `fonts.py::registry_family` + `get_font_path`, restricted to the one
/// branch `_callout_md_renderer` ever reaches (`numeric=False`): a "Source
/// Serif" stack measures against the vendored Source Serif 4 file, every
/// other family — including one we do not vendor — falls back to Inter
/// Variable, the documented "closest honest estimate" default.
fn font_path(fonts_dir: &str, family_css: &str) -> String {
    let primary = family_css
        .split(',')
        .next()
        .unwrap_or("")
        .trim()
        .trim_matches(['\'', '"']);
    let file = if primary.starts_with("Source Serif") {
        "SourceSerif4Variable.ttf"
    } else {
        "InterVariable.ttf"
    };
    format!("{fonts_dir}/{file}")
}

/// `fonts.py::get_mono_font_path` — always Source Code Pro, no family input.
fn mono_font_path(fonts_dir: &str) -> String {
    format!("{fonts_dir}/SourceCodePro-Regular.ttf")
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

struct ContentBlock {
    elements: String,
    style_block: String,
    height: f64,
}

fn render_markdown_block(
    renderer: &SVGRenderer,
    text: &str,
    content_width: f64,
) -> Result<ContentBlock, String> {
    let doc = mdsvg::parser::parse(text);
    let r = renderer.render_content(&doc, content_width, 0.0)?;
    Ok(ContentBlock {
        elements: r.elements,
        style_block: r.style_block,
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
) -> Result<ContentBlock, String> {
    let family = font.family_or("Inter Variable, sans-serif");
    let size = font.size_or(14.0);
    let lines = wrap_plain(fonts_dir, text, content_width, size, family, max_lines)?;
    let line_height = size * LINE_HEIGHT_RATIO;
    let mut xml = Xml::new();
    for (i, line) in lines.iter().enumerate() {
        if i > 0 {
            xml.text("\n  ");
        }
        xml.leaf(
            "text",
            Attrs::new()
                .set("x", "0")
                .set("y", py_float_str(size + i as f64 * line_height))
                .set("font-size", py_float_str(size))
                .set("font-family", family)
                .set("fill", fill)
                .set("text-anchor", "start"),
            line,
        );
    }
    let elements = xml.finish();
    Ok(ContentBlock {
        elements,
        style_block: String::new(),
        height: lines.len() as f64 * line_height,
    })
}

/// `wrap_text_precise(..., ellipsis=True) or [text]` against the font
/// `get_font_measurer(family)` loads.
fn wrap_plain(
    fonts_dir: &str,
    text: &str,
    width: f64,
    size: f64,
    family: &str,
    max_lines: usize,
) -> Result<Vec<String>, String> {
    let path = font_path(fonts_dir, family);
    let measurer = cached_measurer(&path, 0, None);
    if !measurer.is_available() {
        return Err(format!("callout: font file {path} cannot be read to measure text"));
    }
    let (lines, _) = wrap_text_precise(text, width, size, &measurer, Some(max_lines), true, true);
    Ok(if lines.is_empty() { vec![text.to_string()] } else { lines })
}

fn warn_truncated(chart_id: &str, field: &str, text: &str) {
    warn(Diagnostic::new(
        &codes::WARN_CALLOUT_TEXT_TRUNCATED,
        [("chart_id", json!(chart_id)), ("authored_field", json!(field)), ("authored_text", json!(text))],
    ));
}

fn emit_block(
    xml: &mut Xml,
    elements: &str,
    (x, y): (f64, f64),
    clip: Option<(&str, f64, f64)>,
    kind: Option<&str>,
) {
    let mut group = Attrs::new().set("transform", format!("translate({}, {})", py_float_str(x), py_float_str(y)));
    if let Some((id, width, height)) = clip {
        xml.open("defs", Attrs::new());
        xml.open("clipPath", Attrs::new().set("id", id));
        xml.empty(
            "rect",
            Attrs::new()
                .set("x", "0")
                .set("y", "0")
                .set("width", py_float_str(width))
                .set("height", py_float_str(height)),
        );
        xml.close("clipPath");
        xml.close("defs");
        group = group.set("clip-path", format!("url(#{id})"));
    }
    xml.open("g", group.set_some("data-authored-kind", kind));
    xml.raw(elements);
    xml.close("g");
}

/// `callout.py::render_callout_svg`. Returns the SVG and its natural height:
/// the height `render_callout_svg` draws at when no slot height is given
/// (`max(MIN_HEIGHT, content)`), which core's sizing pass reads.
pub fn render(payload: &Payload, fonts: &FontBook) -> Result<(String, f64), String> {
    let _ = fonts; // measurement runs through mdsvg's own font loading, not FontBook
    let r: Resolved = serde_json::from_value(payload.resolved.clone())
        .map_err(|e| format!("callout: payload.resolved: {e}"))?;
    let fonts_dir = payload
        .fonts_dir
        .as_deref()
        .ok_or_else(|| "callout: payload.fonts_dir is required to measure markdown".to_string())?;

    let inline = &r.style;
    let pad_left = inline.padding.left;
    let pad_right = inline.padding.right;
    let pad_top = inline.padding.top;
    let pad_bottom = inline.padding.bottom;
    let section_gap = inline.section_gap;

    let w = payload.width.max(120.0);
    let content_width = (w - pad_left - pad_right).max(40.0);

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

    let title_text = r.title.as_deref().filter(|t| !t.is_empty());
    let (title_block, message_block) = if r.placard.is_none() {
        let title_renderer = callout_md_renderer(
            fonts_dir,
            &inline.title.font,
            &inline.bold_font_weight,
            &title_color,
            &title_color,
        )?;
        let message_renderer = callout_md_renderer(
            fonts_dir,
            &inline.message.font,
            &inline.bold_font_weight,
            &message_color,
            &title_color,
        )?;
        (
            title_text
                .map(|t| render_markdown_block(&title_renderer, t, content_width))
                .transpose()?,
            render_markdown_block(&message_renderer, &r.message, content_width)?,
        )
    } else {
        (
            title_text
                .map(|t| {
                    plain_block(
                        fonts_dir,
                        t,
                        content_width,
                        &inline.title.font,
                        &title_color,
                        MAX_TITLE_LINES,
                    )
                })
                .transpose()?,
            plain_block(
                fonts_dir,
                &r.message,
                content_width,
                &inline.message.font,
                &message_color,
                MAX_MESSAGE_LINES,
            )?,
        )
    };

    let title_size = inline.title.font.size_or(14.0);
    let message_size = inline.message.font.size_or(14.0);
    let message_family = inline.message.font.family_or("Inter Variable, sans-serif");
    let max_title_height = MAX_TITLE_LINES as f64 * title_size * LINE_HEIGHT_RATIO;
    let max_message_height = MAX_MESSAGE_LINES as f64 * message_size * LINE_HEIGHT_RATIO;

    let title_block_height = title_block
        .as_ref()
        .map_or(0.0, |b| b.height.min(max_title_height));
    let message_block_height = message_block.height.min(max_message_height);
    let title_gap = if title_block.is_some() {
        section_gap
    } else {
        0.0
    };

    let message_line_height = message_size * LINE_HEIGHT_RATIO;
    let hint_lines = match r.placard.as_ref().and_then(|p| p.hint.as_deref()) {
        Some(hint) if !hint.is_empty() => wrap_plain(
            fonts_dir,
            hint,
            content_width,
            message_size,
            message_family,
            MAX_HINT_LINES,
        )?,
        _ => Vec::new(),
    };
    let hint_block_height = hint_lines.len() as f64 * message_line_height;
    let doc_url = r.placard.as_ref().and_then(|p| p.doc_url.as_deref());
    let doc_block_height = if doc_url.is_some() {
        message_size * DOC_LINK_SIZE_RATIO * LINE_HEIGHT_RATIO
    } else {
        0.0
    };
    let code = r.placard.as_ref().map(|p| (p.code.as_str(), p.code_url.as_str()));
    let code_badge_height = if code.is_some() {
        title_size * CODE_SIZE_RATIO * LINE_HEIGHT_RATIO
    } else {
        0.0
    };

    let natural_height = pad_top
        + pad_bottom
        + code_badge_height
        + if code.is_some() { section_gap } else { 0.0 }
        + title_block_height
        + title_gap
        + message_block_height
        + if hint_lines.is_empty() { 0.0 } else { section_gap + hint_block_height }
        + if doc_url.is_some() { section_gap + doc_block_height } else { 0.0 };
    let natural_height = natural_height.max(MIN_HEIGHT);
    let h = payload.height.max(natural_height);

    let mut current_y = pad_top;
    let mut style_block = String::new();

    let code_draw = match code {
        Some((code, code_url)) => {
            let code_font_size = title_size * CODE_SIZE_RATIO;
            let draw = (crate::href::checked_href(code_url)?, code, current_y + code_font_size, code_font_size);
            current_y += code_badge_height + section_gap;
            Some(draw)
        }
        None => None,
    };

    let mut title_draw = None;
    if let Some(block) = &title_block {
        style_block.push_str(&block.style_block);
        let translate_y = current_y + inline.title.y_offset;
        let clip_id = if block.height > max_title_height {
            if r.placard.is_none() {
                warn_truncated(&r.id, "title", r.title.as_deref().unwrap_or_default());
            }
            Some(format!("title-clip-{}", callout_hash(&r, w, &title_color, &message_color)))
        } else {
            None
        };
        title_draw = Some((block, translate_y, clip_id));
        current_y += title_block_height + section_gap;
    }

    style_block.push_str(&message_block.style_block);
    let message_translate_y = current_y + inline.message.y_offset;
    let message_clip_id = if message_block.height > max_message_height {
        if r.placard.is_none() {
            warn_truncated(&r.id, "message", &r.message);
        }
        Some(format!("msg-clip-{}", callout_hash(&r, w, &title_color, &message_color)))
    } else {
        None
    };
    current_y += message_block_height;

    let hint_start_y = current_y + section_gap;

    let doc_draw = match doc_url {
        Some(doc_url) => {
            let after_hint_y =
                current_y + if hint_lines.is_empty() { 0.0 } else { section_gap + hint_block_height };
            Some((crate::href::checked_href(doc_url)?, after_hint_y + section_gap))
        }
        None => None,
    };

    // `callout.py`'s own final return block interpolates `w`/`h`/
    // `inline.border.width`/`inline.border.radius` as bare Python floats
    // (`f'width="{w}"'`, never `format_svg_numeric`), so a whole number
    // keeps its `.0` (`rx="4.0"`, not `rx="4"`) — `py_float_str`, not
    // `num_full`. Caught by board_sweep.py's `layout_bg_rect_count`/
    // `chrome_bytes` structural contract once `playground/callout-tones`
    // could compose far enough to reach it.
    let mut xml = Xml::new();
    xml.open(
        "svg",
        Attrs::new()
            .set("xmlns", "http://www.w3.org/2000/svg")
            .set("class", "dbt-chart-callout")
            .set("width", py_float_str(w))
            .set("height", py_float_str(h))
            .set("viewBox", format!("0 0 {} {}", py_float_str(w), py_float_str(h))),
    );
    xml.empty(
        "rect",
        Attrs::new()
            .set("x", "0")
            .set("y", "0")
            .set("width", py_float_str(w))
            .set("height", py_float_str(h))
            .set("fill", &inline.background)
            .set("stroke", &inline.border.color)
            .set("stroke-width", py_float_str(inline.border.width))
            .extend(
                crate::common::border_dash_pairs(
                    inline.border.dash_array.as_deref(),
                    inline.border.line_cap.as_deref(),
                    inline.border.dash_offset,
                )
                .into_iter()
                .fold(Attrs::new(), |attrs, (name, value)| attrs.set(name, value)),
            )
            .set("rx", py_float_str(inline.border.radius)),
    );
    xml.raw(&style_block);

    if let Some((href, code, y, size)) = code_draw {
        xml.open("a", Attrs::new().set("href", href).set("target", "_blank"));
        xml.leaf(
            "text",
            Attrs::new()
                .set("x", py_float_str(pad_left))
                .set("y", py_float_str(y))
                .set("font-size", py_float_str(size))
                .set("font-weight", "normal")
                .set("font-family", "monospace")
                .set("fill", &message_color),
            code,
        );
        xml.close("a");
    }

    if let Some((block, translate_y, clip_id)) = &title_draw {
        let clip = clip_id.as_deref().map(|id| (id, content_width, title_block_height));
        emit_block(&mut xml, &block.elements, (pad_left, *translate_y), clip, Some("title"));
    }
    let message_clip = message_clip_id.as_deref().map(|id| (id, content_width, message_block_height));
    emit_block(&mut xml, &message_block.elements, (pad_left, message_translate_y), message_clip, None);

    for (i, line) in hint_lines.iter().enumerate() {
        xml.leaf(
            "text",
            Attrs::new()
                .set("x", py_float_str(pad_left))
                .set("y", py_float_str(hint_start_y + i as f64 * message_line_height))
                .set("font-size", py_float_str(message_size))
                .set("font-weight", "normal")
                .set("font-style", "italic")
                .set("font-family", message_family)
                .set("fill", &message_color),
            line,
        );
    }

    if let Some((href, y)) = doc_draw {
        xml.open("a", Attrs::new().set("href", href).set("target", "_blank"));
        xml.leaf(
            "text",
            Attrs::new()
                .set("x", py_float_str(pad_left))
                .set("y", py_float_str(y))
                .set("font-size", py_float_str(message_size * DOC_LINK_SIZE_RATIO))
                .set("font-weight", "normal")
                .set("font-family", message_family)
                .set("fill", &title_color),
            "See the docs",
        );
        xml.close("a");
    }
    xml.close("svg");
    let svg = xml.finish();
    Ok((svg, natural_height))
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
fn callout_hash(r: &Resolved, w: f64, title_color: &str, message_color: &str) -> String {
    use md5::{Digest, Md5};
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
        title_color,
        message_color,
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
        let (svg, _) = render(&payload, &test_fonts()).expect("callout renders");
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
        let (svg, _) = render(&payload, &test_fonts()).expect("callout renders");
        assert!(!svg.contains("data-authored-kind=\"title\""), "{svg}");
        assert!(svg.contains("Just a message."), "{svg}");
    }

    #[test]
    fn height_grows_to_fit_natural_content() {
        let long = "Line one.\n\nLine two.\n\nLine three.\n\nLine four.\n\nLine five.";
        let mut json = callout_payload(Some("Title"), long);
        json["height"] = serde_json::json!(60.0);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let (svg, _) = render(&payload, &test_fonts()).expect("callout renders");
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
        let (svg, _) = render(&payload, &test_fonts()).expect("callout renders");
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
        let (svg, _) = render(&payload, &test_fonts()).expect("callout renders");
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
    /// href escape as `escape_svg_text` does (`renderer.py:2280,2290`).
    #[test]
    fn message_image_without_a_height_paints_at_the_fallback_ratio() {
        let message = "![A \"quoted\" <alt> & more](assets/missing.png){width=120}";
        let payload: Payload = serde_json::from_value(callout_payload(None, message)).unwrap();
        let (svg, _) = render(&payload, &test_fonts()).expect("callout renders");
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
        let (svg, _) = render(&payload, &test_fonts()).expect("callout renders");
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
    fn placard_paints_code_badge_plain_message_hint_and_doc_link() {
        let json = placard_payload(
            Some("Check the \"query\"."),
            Some("https://docs.example.com/reference/errors/#err-kpi-no-data"),
        );
        let payload: Payload = serde_json::from_value(json).unwrap();
        let (svg, _) = render(&payload, &test_fonts()).expect("placard renders");
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
        let (svg, _) = render(&payload, &test_fonts()).expect("placard renders");
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
        let (_, bare_height) = render(&bare, &test_fonts()).unwrap();
        let (_, full_height) = render(&full, &test_fonts()).unwrap();
        // hint: gap 8 + one line 13*1.35; doc link: gap 8 + 13*0.85*1.35.
        let expected = 8.0 + 13.0 * 1.35 + 8.0 + 13.0 * 0.85 * 1.35;
        assert!((full_height - bare_height - expected).abs() < 1e-9, "{bare_height} {full_height}");
    }

    #[test]
    fn natural_height_is_independent_of_the_slot_height() {
        let mut json = callout_payload(Some("Title"), "Message.");
        json["height"] = serde_json::json!(0.0);
        let small: Payload = serde_json::from_value(json.clone()).unwrap();
        json["height"] = serde_json::json!(500.0);
        let tall: Payload = serde_json::from_value(json).unwrap();
        let (small_svg, small_height) = render(&small, &test_fonts()).unwrap();
        let (tall_svg, tall_height) = render(&tall, &test_fonts()).unwrap();
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
        let (svg, _) = rendered.expect("placard renders");
        assert!(svg.contains("msg-clip-"), "{svg}");
        assert!(warnings.is_empty(), "{warnings:?}");

        let mut json = callout_payload(None, &long);
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
}
