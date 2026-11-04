//! The error placard core paints into the slot of every chart that failed.
//!
//! The chart's own title sits outside the tile, on the row's shared baseline.
//! The tile is diagonal hairline stripes with one plate on them behind a text
//! block: warning glyph and error code in the code font, the specific message,
//! the hint if any, and a link to the code's entry on the error reference page.
//! Text is plain wrapped `<text>` (identifiers and tracebacks are not markdown)
//! and records no truncation warning, having no authored YAML line to point at.

use serde::Deserialize;

use super::{
    callout_hash, card_rect, card_svg, insets_of, link, text_line, wrap_callout, Bound,
    LINE_HEIGHT_RATIO, MAX_MESSAGE_LINES, MIN_HEIGHT, MIN_WIDTH,
};
use crate::common::Font;
use crate::diagnostic::Diagnostic;
use crate::layout::{Layout, Rect};
use measure::font_files::{font_path, mono_font_path};
use measure::fonts::cached_measurer;
use scene::{Attrs, Node, PathData, Role, Shape, Transform};

const MAX_HINT_LINES: usize = 3;
/// The widest the text block grows, in `ch` (the advance of a "0" in the body
/// font): about 40 characters wraps a typical message onto three or four short
/// lines, a compact block instead of a banner across a wide tile.
const MAX_BLOCK_CH: f64 = 40.0;
/// The narrowest block the panel's inset may squeeze the text to: the inset
/// gives way before the text does.
const MIN_BLOCK_CH: f64 = 24.0;
/// How far the plate sits in from the panel's left edge: `MAX` where the panel
/// is wide enough to keep a `MIN_BLOCK_CH` block, shrinking to `MIN` (the
/// inset of a tight tile) as the width runs out.
const INSET_X_MAX: f64 = 16.0;
const INSET_X_MIN: f64 = 6.0;
/// How far the plate sits down from the panel's top edge, which a slot shorter
/// than the block plus `MAX` squeezes toward `MIN` before anything clips. The
/// block is top-anchored, never centered.
const INSET_Y_MAX: f64 = 28.0;
const INSET_Y_MIN: f64 = 12.0;
/// Space between the lines of the text block.
const BLOCK_GAP: f64 = 4.0;
pub(super) const PLATE_PAD_X: f64 = 12.0;
const PLATE_PAD_Y: f64 = 10.0;
const PLATE_RADIUS: f64 = 3.0;
/// The stripes' width and their pitch across the diagonal.
const STRIPE_WIDTH: f64 = 1.0;
const STRIPE_PITCH: f64 = 8.0;
const ICON_GAP: f64 = 6.0;
/// The warning glyph's own box, which `ICON_*` paths are drawn in.
const ICON_W: f64 = 13.0;
const ICON_H: f64 = 12.0;
/// The glyph's height as a multiple of the body size.
const ICON_SIZE_RATIO: f64 = 1.1;
const ICON_STROKE: f64 = 1.2;

/// `render_error_placard`'s payload.
#[derive(Deserialize)]
pub(super) struct Data {
    code: String,
    /// The code's own anchor on the error reference page.
    code_url: String,
    hint: Option<String>,
    /// The failed chart's own title, kept above the tile.
    #[serde(default)]
    chart_title: Option<String>,
    #[serde(default)]
    chart_title_font: Option<Font>,
    /// Where the title's first baseline sits in the slot, and where the tile
    /// starts below it: the row's title band decides both.
    chart_title_baseline: f64,
    tile_top: f64,
    /// The left edge of the title and the text block, shared with a healthy
    /// chart's title.
    content_left: f64,
}

pub(super) struct Placard {
    code: String,
    href: String,
    hint: Option<String>,
    link_text: String,
    chart_title: Option<ChartTitle>,
    stripe_color: String,
    code_family: String,
    content_left: f64,
}

/// The failed chart's title, painted above the tile in the chart title font.
struct ChartTitle {
    text: String,
    font: Font,
    size: f64,
    family: String,
    color: String,
    baseline: f64,
    tile_top: f64,
}

pub(super) fn bind(
    data: Data,
    stripe_color: Option<&str>,
    code_family: &str,
) -> Result<Placard, Diagnostic> {
    let stripe_color = stripe_color
        .ok_or("callout: style.stripe_color missing")?
        .to_string();
    let chart_title = match data.chart_title.filter(|t| !t.is_empty()) {
        Some(text) => {
            let font = data
                .chart_title_font
                .ok_or("callout: placard.chart_title_font missing")?;
            let size = font.size.ok_or("callout: placard.chart_title_font.size missing")?;
            let family = font
                .family
                .clone()
                .ok_or("callout: placard.chart_title_font.family missing")?;
            let case = font.case.as_deref().unwrap_or("none");
            let text = display_text::case::apply_case(&text, case)?;
            Some(ChartTitle {
                text,
                size,
                family,
                baseline: data.chart_title_baseline,
                tile_top: data.tile_top,
                color: font
                    .color
                    .clone()
                    .ok_or("callout: placard.chart_title_font.color missing")?,
                font,
            })
        }
        None => None,
    };
    Ok(Placard {
        href: crate::href::checked_href(&data.code_url)?.to_string(),
        code: data.code,
        hint: data.hint.filter(|hint| !hint.is_empty()),
        link_text: display_text::language::tr("placard.about_this_error", None, &[])?,
        chart_title,
        stripe_color,
        code_family: code_family.to_string(),
        content_left: data.content_left,
    })
}

/// What a width decides, shared by `height_for_width` and `layout`.
pub(super) struct Composed {
    width: f64,
    /// The plate's left edge, and its text a plate padding further in.
    inset_x: f64,
    tile_top: f64,
    title_line: Option<String>,
    body_size: f64,
    body_family: String,
    icon_height: f64,
    code_lines: Vec<String>,
    message_lines: Vec<String>,
    hint_lines: Vec<String>,
    head_height: f64,
    plate_height: f64,
    /// The widest line of the block, which the plate hugs.
    widest: f64,
    pub(super) natural_height: f64,
}

/// The code split after its hyphens, packed greedily into `max_width`; never
/// truncated, so a segment wider than the width stands on a line of its own.
fn wrap_code(code: &str, max_width: f64, width_of: impl Fn(&str) -> f64) -> Vec<String> {
    let mut lines: Vec<String> = Vec::new();
    for segment in code.split_inclusive('-') {
        match lines.last_mut() {
            Some(last) if width_of(&format!("{last}{segment}")) <= max_width => {
                last.push_str(segment)
            }
            _ => lines.push(segment.to_string()),
        }
    }
    lines
}

pub(super) fn compose(bound: &Bound, width: f64) -> Result<Composed, Diagnostic> {
    let placard = bound.placard.as_ref().expect("a placard bound");
    let style = &bound.style;
    let fonts_dir = bound.fonts_dir.as_str();

    let w = width.max(MIN_WIDTH);
    let left = placard.content_left;
    let body_size = style.message.font.size_or(14.0);
    let body_family = style
        .message
        .font
        .family_or("Inter Variable, sans-serif")
        .to_string();
    let line_height = body_size * LINE_HEIGHT_RATIO;
    let icon_height = body_size * ICON_SIZE_RATIO;
    let icon_width = icon_height * ICON_W / ICON_H;

    let body_measurer = cached_measurer(&font_path(fonts_dir, &body_family), 0, None);
    let mono_measurer = cached_measurer(&mono_font_path(fonts_dir), 0, None);
    if !body_measurer.is_available() || !mono_measurer.is_available() {
        return Err("callout: a placard font file cannot be read to measure text".into());
    }
    let body_width = |line: &str| body_measurer.measure(line, body_size);
    let ch = body_width("0");
    let inset_x = ((w - MIN_BLOCK_CH * ch - 2.0 * PLATE_PAD_X) / 2.0).clamp(INSET_X_MIN, INSET_X_MAX);
    let room = (w - 2.0 * (inset_x + PLATE_PAD_X)).max(40.0);
    let text_width = room.min(MAX_BLOCK_CH * ch);
    let mono_width = |line: &str| mono_measurer.measure(line, body_size);

    // The code is an identifier: it takes the room the tile has, past the prose
    // column, and wraps only when the tile itself runs out.
    let code_lines = wrap_code(&placard.code, room - icon_width - ICON_GAP, mono_width);
    let message_lines = wrap_callout(
        fonts_dir,
        &bound.message,
        text_width,
        body_size,
        &body_family,
        MAX_MESSAGE_LINES,
    )?;
    let hint_lines = match placard.hint.as_deref() {
        Some(hint) => wrap_callout(fonts_dir, hint, text_width, body_size, &body_family, MAX_HINT_LINES)?,
        None => Vec::new(),
    };

    let head_height = (code_lines.len() as f64 * line_height).max(icon_height);
    let content_height = head_height
        + BLOCK_GAP
        + message_lines.len() as f64 * line_height
        + if hint_lines.is_empty() {
            0.0
        } else {
            BLOCK_GAP + hint_lines.len() as f64 * line_height
        }
        + BLOCK_GAP
        + line_height;

    let widest = code_lines
        .iter()
        .map(|l| icon_width + ICON_GAP + mono_width(l))
        .chain(message_lines.iter().map(|l| body_width(l)))
        .chain(hint_lines.iter().map(|l| body_width(l)))
        .chain(std::iter::once(body_width(&placard.link_text)))
        .fold(0.0, f64::max);

    let (title_line, tile_top) = match placard.chart_title.as_ref() {
        Some(t) => {
            let lines = wrap_callout(
                fonts_dir,
                &t.text,
                w - left,
                t.size,
                &t.family,
                1,
            )?;
            (lines.into_iter().next(), t.tile_top)
        }
        None => (None, 0.0),
    };

    let plate_height = 2.0 * PLATE_PAD_Y + content_height;
    let natural_height = tile_top + INSET_Y_MAX + plate_height + style.padding.bottom;
    Ok(Composed {
        width: w,
        inset_x,
        plate_height,
        tile_top,
        title_line,
        body_size,
        body_family,
        icon_height,
        code_lines,
        message_lines,
        hint_lines,
        head_height,
        widest,
        natural_height: natural_height.max(MIN_HEIGHT),
    })
}

/// The warning glyph: an outlined triangle with a bang, drawn from primitives
/// in a 13 x 12 box scaled to `height`, never a font glyph.
fn warning_glyph(x: f64, y: f64, height: f64, ink: &str) -> Node {
    let scale = height / ICON_H;
    let outline = PathData::new().move_to(6.5, 0.9).line_to(12.3, 11.1).line_to(0.7, 11.1).close();
    let bang = PathData::new().move_to(6.5, 4.3).line_to(6.5, 7.7);
    let parts = vec![
        Node::path(
            Role::Chrome,
            outline,
            Attrs::new().quick("fill", "none").quick("stroke", ink).num("stroke-width", ICON_STROKE).quick("stroke-linejoin", "round"),
        ),
        Node::path(
            Role::Chrome,
            bang,
            Attrs::new().quick("fill", "none").quick("stroke", ink).num("stroke-width", ICON_STROKE).quick("stroke-linecap", "round"),
        ),
        Node::circle(Role::Chrome, (6.5, 9.3), 0.7, Attrs::new().quick("fill", ink)),
    ];
    Node::group(Role::Chrome, Attrs::new(), parts).translate(x, y).with_transform(Transform::Scale(scale))
}

/// The stripe fill: 45 degree hairlines, a static pattern a raster export keeps.
fn stripes(id: &str, color: &str) -> Node {
    let tile = Node::rect(Role::Chrome, (0.0, 0.0, STRIPE_WIDTH, STRIPE_PITCH), Attrs::new().quick("fill", color));
    let attrs = Attrs::new().quick("id", id).quick("patternUnits", "userSpaceOnUse");
    let pattern = Node::new(Role::Chrome, attrs, Shape::Pattern { w: STRIPE_PITCH, h: STRIPE_PITCH, transform: Some(Transform::Rotate { deg: 45.0, about: None }), children: vec![tile] });
    Node::new(Role::Chrome, Attrs::new(), Shape::Defs(vec![pattern]))
}

pub(super) fn draw(bound: &Bound, c: Composed, height: f64) -> Result<Layout, Diagnostic> {
    let placard = bound.placard.as_ref().expect("a placard bound");
    let style = &bound.style;
    let ink = bound.message_color.as_str();
    let (w, h) = (c.width, height);
    let title_left = placard.content_left;
    let line_height = c.body_size * LINE_HEIGHT_RATIO;
    let mut children = Vec::new();

    children.push(card_rect(style, w, c.tile_top, h));
    let stripe_tag: String = placard.stripe_color.chars().filter(char::is_ascii_alphanumeric).collect();
    let stripe_id = format!("err-stripes-{}-{stripe_tag}", callout_hash(bound, w));
    children.push(stripes(&stripe_id, &placard.stripe_color));
    children.push(Node::new(
        Role::Chrome,
        Attrs::new().quick("fill", format!("url(#{stripe_id})")),
        Shape::Rect { x: 0.0, y: c.tile_top, w, h: h - c.tile_top, rx: style.border.radius },
    ));

    if let (Some(title), Some(line)) = (&placard.chart_title, &c.title_line) {
        children.push(text_line(
            Role::Title,
            (title_left, title.baseline),
            Attrs::new()
                .num("font-size", title.size)
                .quick("font-weight", title.font.weight_str())
                .quick("font-family", title.family.as_str())
                .quick("fill", title.color.as_str())
                .quick("data-authored-kind", "title"),
            line,
        ));
    }

    // Top-anchored, inset by what the slot can spare below the plate.
    let inset_y = (h - c.tile_top - c.plate_height - style.padding.bottom).clamp(INSET_Y_MIN, INSET_Y_MAX);
    let plate_top = c.tile_top + inset_y;
    let plate_left = c.inset_x;
    let left = plate_left + PLATE_PAD_X;
    children.push(Node::new(
        Role::Chrome,
        Attrs::new().quick("fill", style.background.as_str()),
        Shape::Rect { x: plate_left, y: plate_top, w: c.widest + 2.0 * PLATE_PAD_X, h: c.plate_height, rx: PLATE_RADIUS },
    ));

    let mut y = plate_top + PLATE_PAD_Y;
    let icon_width = c.icon_height * ICON_W / ICON_H;
    children.push(warning_glyph(
        left,
        y + (line_height - c.icon_height) / 2.0,
        c.icon_height,
        ink,
    ));
    for (i, line) in c.code_lines.iter().enumerate() {
        children.push(text_line(
            Role::Label,
            (left + icon_width + ICON_GAP, y + c.body_size + i as f64 * line_height),
            Attrs::new()
                .num("font-size", c.body_size)
                .quick("font-weight", "normal")
                .quick("font-family", placard.code_family.as_str())
                .quick("fill", ink),
            line,
        ));
    }
    y += c.head_height + BLOCK_GAP;

    let mut body_lines = |lines: &[String], y: &mut f64| {
        for (i, line) in lines.iter().enumerate() {
            children.push(text_line(
                Role::Label,
                (left, *y + c.body_size + i as f64 * line_height),
                Attrs::new()
                    .num("font-size", c.body_size)
                    .quick("font-weight", "normal")
                    .quick("font-family", c.body_family.as_str())
                    .quick("fill", ink),
                line,
            ));
        }
        *y += lines.len() as f64 * line_height;
    };
    body_lines(&c.message_lines, &mut y);
    if !c.hint_lines.is_empty() {
        y += BLOCK_GAP;
        body_lines(&c.hint_lines, &mut y);
    }
    y += BLOCK_GAP;
    children.push(link(
        &placard.href,
        text_line(
            Role::Label,
            (left, y + c.body_size),
            Attrs::new()
                .num("font-size", c.body_size)
                .quick("font-weight", "normal")
                .quick("font-family", c.body_family.as_str())
                .quick("text-decoration", "underline")
                .quick("fill", ink),
            &placard.link_text,
        ),
    ));

    let data_box = Rect {
        x: left,
        y: plate_top,
        width: c.widest,
        height: h - plate_top - style.padding.bottom,
    };
    Ok(Layout {
        insets: insets_of(&data_box, w, style.padding.bottom),
        data_box,
        nodes: vec![card_svg(w, h, children)],
        diagnostics: Vec::new(),
        title_pos: None,
    })
}
