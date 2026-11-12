//! A failed chart's error card: the striped tile, the plate on it and the text block on the plate
//! (warning glyph and code, message, hint, the link to the code's reference entry), and the chart's
//! own title above the tile.
//!
//! The error tile ([`ErrorTile::of`]) is read off the board scope's total style; the caller supplies
//! the title's baseline. The words come from `bind::failed`. The card's root `<svg class="dbt-chart-callout">` is part of the DOM contract and is
//! emitted here. Text is plain wrapped `<text>` (identifiers and tracebacks are not markdown) and
//! records no truncation warning, having no authored YAML line to point at.
//!
//! The constants and rules below are production's, each cited at its source
//! (`chart-renderer/src/families/callout/placard.rs`, `callout.rs`): the card is pinned byte for byte
//! to recorded production output (`tests/it/placard_production.json`).
//!
//! `plan` is the lines at a width and the natural height; `draw` the nodes. [`ErrorTile::of`] reads the
//! theme's `panel` and `stripe` roles, the chart title's ink and the board's emoji family;
//! `tests/it/error_tile.rs` pins each law on edited styles, and `tests/it/failed_card.rs` replays
//! `placard_production.json`, recorded by `record_placard.py`.

use bind::failed::FailedText;
use bind::font_stack::with_emoji;
use board_model::facet::FontWeightValue;
use board_model::palette::{self, ThemeColors};
use board_model::style::board::EmojiMode;
use board_model::style::callout::CalloutElementStyle;
use board_model::style::style::Style;
use diagnostic::{diag, Diagnostic};
use indexmap::IndexMap;
use measure::font_files::{font_file, MONO_FILE};
use measure::shaped::FontBook;
use measure::wrap::wrap_plain;
use scene::{Attrs, Chars, Escape, Extent, Node, PathData, Role, Run, Shape, Transform};

use crate::legacy;

/// callout.rs:45-46. A card is never narrower or shorter.
pub(super) const MIN_WIDTH: f64 = 120.0;
pub(super) const MIN_HEIGHT: f64 = 60.0;
/// callout.rs:47. Line pitch as a multiple of the body size.
pub(super) const LINE_HEIGHT_RATIO: f64 = 1.35;
/// callout.rs:48 and placard.rs:23.
pub(super) const MAX_MESSAGE_LINES: usize = 12;
const MAX_HINT_LINES: usize = 3;
/// placard.rs:27. The widest the text block grows, in `ch` (the advance of a "0" in the body font).
const MAX_BLOCK_CH: f64 = 40.0;
/// placard.rs:30. The narrowest block the panel's inset may squeeze the text to.
const MIN_BLOCK_CH: f64 = 24.0;
/// placard.rs:34-35. How far the plate sits in from the left edge: `MAX` while a `MIN_BLOCK_CH` block fits.
const INSET_X_MAX: f64 = 16.0;
const INSET_X_MIN: f64 = 6.0;
/// placard.rs:39-40. How far the plate sits below the tile's top; a short slot squeezes it toward `MIN`.
const INSET_Y_MAX: f64 = 28.0;
const INSET_Y_MIN: f64 = 12.0;
/// placard.rs:42-45. Space between the text block's parts, and the plate's padding and corner.
const BLOCK_GAP: f64 = 4.0;
const PLATE_PAD_X: f64 = 12.0;
const PLATE_PAD_Y: f64 = 10.0;
const PLATE_RADIUS: f64 = 3.0;
/// placard.rs:47-48. The stripes' width and their pitch across the diagonal.
const STRIPE_WIDTH: f64 = 1.0;
const STRIPE_PITCH: f64 = 8.0;
/// placard.rs:49-55. The warning glyph: its box, its size as a multiple of the body size, its stroke.
const ICON_GAP: f64 = 6.0;
const ICON_W: f64 = 13.0;
const ICON_H: f64 = 12.0;
const ICON_SIZE_RATIO: f64 = 1.1;
const ICON_STROKE: f64 = 1.2;

/// A CSS `font-weight` as the resolved style carries it.
#[derive(Debug, Clone, PartialEq)]
pub enum Weight {
    Number(f64),
    Css(String),
}

impl Weight {
    /// A resolved weight in the form it was authored in.
    pub(crate) fn of(weight: &FontWeightValue) -> Self {
        match weight {
            FontWeightValue::Number(n) => Weight::Number(*n),
            FontWeightValue::Name(name) => Weight::Css(name.clone()),
        }
    }

    /// The `font-weight` attribute (common.rs:245): a number as a whole number, a string as authored.
    pub(crate) fn attr(&self) -> String {
        match self {
            Weight::Number(n) => format!("{}", *n as i64),
            Weight::Css(css) => css.clone(),
        }
    }

    /// What the stripe id's hash reads (callout.rs:`hash_weight_str`).
    fn hashed(&self) -> String {
        match self {
            Weight::Number(n) => legacy::hashed_number(*n),
            Weight::Css(css) => css.clone(),
        }
    }
}

/// A text face: the CSS family stack, size, weight and ink.
#[derive(Debug, Clone, PartialEq)]
pub struct Face {
    pub family: String,
    pub size: f64,
    pub weight: Weight,
    pub color: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Border {
    pub width: f64,
    pub color: String,
    pub radius: f64,
    pub dash_array: Option<Vec<f64>>,
    pub line_cap: Option<String>,
    pub dash_offset: Option<f64>,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Padding {
    pub top: f64,
    pub right: f64,
    pub bottom: f64,
    pub left: f64,
}

/// The resolved error tile (`ResolvedErrorTileStyle`). `tone`, the padding but its bottom, the
/// section gap and `title` paint nothing here; they only key the stripe pattern's id, as production's
/// callout hash does.
#[derive(Debug, Clone, PartialEq)]
pub struct ErrorTile {
    pub tone: String,
    pub background: String,
    pub border: Border,
    pub padding: Padding,
    pub section_gap: f64,
    pub title: Face,
    pub message: Face,
    pub code_family: String,
    pub stripe_color: String,
}

impl Face {
    /// A resolved font as a face: its stack drawn under the board's emoji mode, its weight in the
    /// form it was authored in.
    pub fn of(family: &str, size: f64, weight: &FontWeightValue, color: &str, emoji: &EmojiMode) -> Self {
        Self {
            family: with_emoji(family, emoji),
            size,
            weight: Weight::of(weight),
            color: color.to_string(),
        }
    }
}

/// The palette bindings a role-indirected token resolves against, as the theme names them.
pub(super) fn bound_palettes(style: &Style) -> IndexMap<String, String> {
    style.palettes.iter().map(|(role, name)| (role.clone(), name.as_str().to_string())).collect()
}

/// The theme's code font as markdown code and a placard's code line paint it (`code_family` of the
/// chart style context), under the board's emoji mode.
pub(crate) fn code_family(style: &Style) -> Result<String, Diagnostic> {
    let family = style.text.code.boxed.font.as_ref().and_then(|font| font.family.as_ref()).ok_or_else(|| diag::err_internal("the theme's `text.code.font.family` is unset"))?;
    Ok(with_emoji(family.as_str(), &style.font.emoji))
}

impl ErrorTile {
    /// The tile a failed chart draws on `style`, the board scope's total style
    /// (`_build_resolved_error_tile`): the callout's structure on the theme's `panel` fill with
    /// `stripe` hairlines, no border width, all text in the chart title's ink. No tone color: a
    /// failed chart is unavailable, not an alarm. Every font stack carries the board's emoji family.
    pub fn of(style: &Style) -> Result<Self, Diagnostic> {
        let palettes = bound_palettes(style);
        let theme = ThemeColors { palettes: &palettes, roles: &style.roles, single_series: None, category: None };
        let role = |name: &str| palette::color_from_theme(name, &theme).map_err(|e| diag::err_internal(format!("the error tile's `{name}` role does not resolve: {e}")));
        let (panel, stripe) = (role("panel")?, role("stripe")?);
        let callout = &style.charts.callout;
        let ink = style.charts.title.font.color.as_str();
        let face = |element: &CalloutElementStyle| {
            let font = &element.font;
            Face::of(font.family.as_str(), font.size, &font.weight, ink, &style.font.emoji)
        };
        let code = code_family(style)?;
        let (border, pad) = (&callout.border, &callout.padding);
        Ok(Self {
            tone: callout.tone.as_str().to_string(),
            background: panel.clone(),
            border: Border {
                width: 0.0,
                color: panel,
                radius: border.radius,
                dash_array: border.dash_array.clone(),
                line_cap: border.line_cap.as_ref().map(|cap| cap.as_str().to_string()),
                dash_offset: border.dash_offset,
            },
            padding: Padding { top: pad.top, right: pad.right, bottom: pad.bottom, left: pad.left },
            section_gap: callout.section_gap,
            title: face(&callout.title),
            message: face(&callout.message),
            code_family: code,
            stripe_color: stripe,
        })
    }
}

/// The failed chart's own title, drawn above the tile on the row's shared baseline. `text` is as it
/// draws (its letter case applied); `tile_top` is where the tile starts below it.
#[derive(Debug, Clone, PartialEq)]
pub struct ChartTitle {
    pub text: String,
    pub face: Face,
    pub baseline: f64,
    pub tile_top: f64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ErrorCard {
    pub text: FailedText,
    pub tile: ErrorTile,
    /// The left edge of the title, shared with a healthy chart's title.
    pub content_left: f64,
    pub title: Option<ChartTitle>,
}

/// What a width decides, shared by the natural height and the drawing.
#[derive(Debug, Clone, PartialEq)]
pub struct Plan {
    width: f64,
    /// The plate's left edge; its text a plate padding further in.
    inset_x: f64,
    tile_top: f64,
    title_line: Option<String>,
    icon_height: f64,
    code_lines: Vec<String>,
    message_lines: Vec<String>,
    hint_lines: Vec<String>,
    head_height: f64,
    plate_height: f64,
    /// The widest line of the block, which the plate hugs.
    widest: f64,
    /// The height the card draws at when its slot gives it no more.
    pub natural_height: f64,
}

/// placard.rs:169. The code split after its hyphens, packed greedily into `max_width`; never cut, so
/// a segment wider than the width stands on a line of its own.
fn wrap_code(code: &str, max_width: f64, width_of: impl Fn(&str) -> f64) -> Vec<String> {
    let mut lines: Vec<String> = Vec::new();
    for segment in code.split_inclusive('-') {
        match lines.last_mut() {
            Some(last) if width_of(&format!("{last}{segment}")) <= max_width => last.push_str(segment),
            _ => lines.push(segment.to_string()),
        }
    }
    lines
}

/// The card's lines at `width` (floored at the minimum card width). `book` holds the served
/// faces; the body face is the one `measure::font_files::font_file` picks for the message family, the
/// code's always Source Code Pro, as production measures them. The widths are the faces' raw
/// advances, not `measure::shaped`: production bakes the card's wraps from raw widths, and who
/// measures decides the measurer (the font measurement policy).
pub fn plan(card: &ErrorCard, book: &FontBook, width: f64) -> Result<Plan, Diagnostic> {
    let tile = &card.tile;
    let w = width.max(MIN_WIDTH);
    let body = &tile.message;
    let line_height = body.size * LINE_HEIGHT_RATIO;
    let icon_height = body.size * ICON_SIZE_RATIO;
    let icon_width = icon_height * ICON_W / ICON_H;

    let body_measurer = book.file(font_file(&body.family)).map_err(diag::err_internal)?.measurer(0, None);
    let mono_measurer = book.file(MONO_FILE).map_err(diag::err_internal)?.measurer(0, None);
    let body_width = |line: &str| body_measurer.measure(line, body.size);
    let mono_width = |line: &str| mono_measurer.measure(line, body.size);
    let ch = body_width("0");
    let inset_x = ((w - MIN_BLOCK_CH * ch - 2.0 * PLATE_PAD_X) / 2.0).clamp(INSET_X_MIN, INSET_X_MAX);
    let room = (w - 2.0 * (inset_x + PLATE_PAD_X)).max(40.0);
    let text_width = room.min(MAX_BLOCK_CH * ch);

    // The code is an identifier: it takes the room the tile has, past the prose column, and wraps
    // only when the tile itself runs out.
    let code_lines = wrap_code(&card.text.code, room - icon_width - ICON_GAP, mono_width);
    let message_lines = wrap_plain(&body_measurer, &card.text.message, text_width, body.size, MAX_MESSAGE_LINES);
    let hint_lines = match card.text.hint.as_deref() {
        Some(hint) => wrap_plain(&body_measurer, hint, text_width, body.size, MAX_HINT_LINES),
        None => Vec::new(),
    };

    let head_height = (code_lines.len() as f64 * line_height).max(icon_height);
    let content_height = head_height
        + BLOCK_GAP
        + message_lines.len() as f64 * line_height
        + if hint_lines.is_empty() { 0.0 } else { BLOCK_GAP + hint_lines.len() as f64 * line_height }
        + BLOCK_GAP
        + line_height;
    let widest = code_lines
        .iter()
        .map(|l| icon_width + ICON_GAP + mono_width(l))
        .chain(message_lines.iter().map(|l| body_width(l)))
        .chain(hint_lines.iter().map(|l| body_width(l)))
        .chain(std::iter::once(body_width(&card.text.link_text)))
        .fold(0.0, f64::max);

    let (title_line, tile_top) = match &card.title {
        Some(t) => {
            if t.text.is_empty() {
                return Err("callout: the chart title is empty; a title-less card passes no title".into());
            }
            // One line, ending in an ellipsis when cut.
            let title_measurer = book.file(font_file(&t.face.family)).map_err(diag::err_internal)?.measurer(0, None);
            let lines = wrap_plain(&title_measurer, &t.text, w - card.content_left, t.face.size, 1);
            (lines.into_iter().next(), t.tile_top)
        }
        None => (None, 0.0),
    };

    let plate_height = 2.0 * PLATE_PAD_Y + content_height;
    let natural_height = tile_top + INSET_Y_MAX + plate_height + tile.padding.bottom;
    Ok(Plan {
        width: w,
        inset_x,
        tile_top,
        title_line,
        icon_height,
        code_lines,
        message_lines,
        hint_lines,
        head_height,
        plate_height,
        widest,
        natural_height: natural_height.max(MIN_HEIGHT),
    })
}

fn text_line(role: Role, at: (f64, f64), attrs: Attrs, text: &str) -> Node {
    let runs = vec![Run::Chars(Chars::new(text, Escape::Quick))];
    Node::new(role, attrs, Shape::Text { at, bounds: None, runs })
}

/// placard.rs:284. The warning glyph: an outlined triangle with a bang, drawn from primitives in a
/// 13 x 12 box scaled to `height`, never a font glyph.
fn warning_glyph(x: f64, y: f64, height: f64, ink: &str) -> Node {
    let scale = height / ICON_H;
    let outline = PathData::new().move_to(6.5, 0.9).line_to(12.3, 11.1).line_to(0.7, 11.1).close();
    let bang = PathData::new().move_to(6.5, 4.3).line_to(6.5, 7.7);
    let stroked = || Attrs::new().quick("fill", "none").quick("stroke", ink).num("stroke-width", ICON_STROKE);
    let parts = vec![
        Node::path(Role::Chrome, outline, stroked().quick("stroke-linejoin", "round")),
        Node::path(Role::Chrome, bang, stroked().quick("stroke-linecap", "round")),
        Node::circle(Role::Chrome, (6.5, 9.3), 0.7, Attrs::new().quick("fill", ink)),
    ];
    Node::group(Role::Chrome, Attrs::new(), parts).translate(x, y).with_transform(Transform::Scale(scale))
}

/// placard.rs:305. The stripe fill: 45 degree hairlines, a static pattern a raster export keeps.
fn stripes(id: &str, color: &str) -> Node {
    let tile = Node::rect(Role::Chrome, (0.0, 0.0, STRIPE_WIDTH, STRIPE_PITCH), Attrs::new().quick("fill", color));
    let attrs = Attrs::new().quick("id", id).quick("patternUnits", "userSpaceOnUse");
    let pattern = Node::new(
        Role::Chrome,
        attrs,
        Shape::Pattern { w: STRIPE_PITCH, h: STRIPE_PITCH, transform: Some(Transform::Rotate { deg: 45.0, about: None }), children: vec![tile] },
    );
    Node::new(Role::Chrome, Attrs::new(), Shape::Defs(vec![pattern]))
}

/// callout.rs:541 (`callout_hash`). An md5 over the callout inputs, over-keyed rather than minimal; a
/// placard has no callout title, so that part is empty.
fn stripe_hash(card: &ErrorCard, width: f64) -> String {
    let tile = &card.tile;
    let number = legacy::hashed_number;
    legacy::callout_hash(&[
        "",
        card.text.message.as_str(),
        tile.tone.as_str(),
        &number(width),
        &number(tile.padding.left),
        &number(tile.padding.right),
        &number(tile.padding.top),
        &number(tile.padding.bottom),
        &number(tile.section_gap),
        tile.title.family.as_str(),
        &number(tile.title.size),
        &tile.title.weight.hashed(),
        tile.message.family.as_str(),
        &number(tile.message.size),
        &tile.message.weight.hashed(),
        tile.title.color.as_str(),
        tile.message.color.as_str(),
    ])
}

/// callout.rs:460. The card's rounded rectangle from `top` to the slot's bottom.
pub(super) fn card_rect(background: &str, border: &Border, w: f64, top: f64, h: f64) -> Node {
    let attrs = Attrs::new()
        .quick("fill", background)
        .quick("stroke", border.color.as_str())
        .num("stroke-width", border.width);
    // common.rs:5890 (`border_dash_pairs`): the dash pattern, its cap and offset, only with a dash array.
    let attrs = match &border.dash_array {
        None => attrs,
        Some(dashes) => {
            let dashes = dashes.iter().map(|v| d3rs::py_float::format_g(*v)).collect::<Vec<_>>().join(",");
            let attrs = attrs.quick("stroke-dasharray", dashes);
            let attrs = match &border.line_cap {
                Some(cap) => attrs.quick("stroke-linecap", cap.as_str()),
                None => attrs,
            };
            match border.dash_offset {
                Some(offset) => attrs.quick("stroke-dashoffset", d3rs::py_float::format_g(offset)),
                None => attrs,
            }
        }
    };
    Node::new(Role::Chrome, attrs, Shape::Rect { x: 0.0, y: top, w, h: h - top, rx: border.radius })
}

/// The card as nodes: its root `<svg class="dbt-chart-callout">` in a slot `height` tall (never less
/// than the plan's natural height).
pub fn draw(card: &ErrorCard, plan: &Plan, height: f64) -> Vec<Node> {
    let tile = &card.tile;
    let (w, h) = (plan.width, height.max(plan.natural_height));
    let ink = tile.message.color.as_str();
    let body = &tile.message;
    let line_height = body.size * LINE_HEIGHT_RATIO;
    let mut children = vec![card_rect(&tile.background, &tile.border, w, plan.tile_top, h)];

    let stripe_tag: String = tile.stripe_color.chars().filter(char::is_ascii_alphanumeric).collect();
    let stripe_id = format!("err-stripes-{}-{stripe_tag}", stripe_hash(card, w));
    children.push(stripes(&stripe_id, &tile.stripe_color));
    children.push(Node::new(
        Role::Chrome,
        Attrs::new().quick("fill", format!("url(#{stripe_id})")),
        Shape::Rect { x: 0.0, y: plan.tile_top, w, h: h - plan.tile_top, rx: tile.border.radius },
    ));

    if let (Some(title), Some(line)) = (&card.title, &plan.title_line) {
        children.push(text_line(
            Role::Title,
            (card.content_left, title.baseline),
            Attrs::new()
                .num("font-size", title.face.size)
                .quick("font-weight", title.face.weight.attr())
                .quick("font-family", title.face.family.as_str())
                .quick("fill", title.face.color.as_str())
                .quick("data-authored-kind", "title"),
            line,
        ));
    }

    // Top-anchored, inset by what the slot can spare below the plate.
    let inset_y = (h - plan.tile_top - plan.plate_height - tile.padding.bottom).clamp(INSET_Y_MIN, INSET_Y_MAX);
    let plate_top = plan.tile_top + inset_y;
    let left = plan.inset_x + PLATE_PAD_X;
    children.push(Node::new(
        Role::Chrome,
        Attrs::new().quick("fill", tile.background.as_str()),
        Shape::Rect { x: plan.inset_x, y: plate_top, w: plan.widest + 2.0 * PLATE_PAD_X, h: plan.plate_height, rx: PLATE_RADIUS },
    ));

    let mut y = plate_top + PLATE_PAD_Y;
    let icon_width = plan.icon_height * ICON_W / ICON_H;
    children.push(warning_glyph(left, y + (line_height - plan.icon_height) / 2.0, plan.icon_height, ink));
    let face = |family: &str| {
        Attrs::new().num("font-size", body.size).quick("font-weight", "normal").quick("font-family", family.to_string())
    };
    for (i, line) in plan.code_lines.iter().enumerate() {
        children.push(text_line(
            Role::Label,
            (left + icon_width + ICON_GAP, y + body.size + i as f64 * line_height),
            face(&tile.code_family).quick("fill", ink),
            line,
        ));
    }
    y += plan.head_height + BLOCK_GAP;

    let mut block = |lines: &[String], y: &mut f64| {
        for (i, line) in lines.iter().enumerate() {
            children.push(text_line(
                Role::Label,
                (left, *y + body.size + i as f64 * line_height),
                face(&body.family).quick("fill", ink),
                line,
            ));
        }
        *y += lines.len() as f64 * line_height;
    };
    block(&plan.message_lines, &mut y);
    if !plan.hint_lines.is_empty() {
        y += BLOCK_GAP;
        block(&plan.hint_lines, &mut y);
    }
    y += BLOCK_GAP;
    let link_text = text_line(
        Role::Label,
        (left, y + body.size),
        face(&body.family).quick("text-decoration", "underline").quick("fill", ink),
        &card.text.link_text,
    );
    let link_attrs = Attrs::new().quick("href", card.text.href.as_str()).quick("target", "_blank");
    children.push(Node::new(Role::Chrome, link_attrs, Shape::Link(vec![link_text])));

    let svg = Attrs::new().quick("class", "dbt-chart-callout");
    vec![Node::new(Role::Chrome, svg, Shape::Svg { extent: Extent { width: w, height: h }, children })]
}

impl crate::family::Family for ErrorCard {
    type Plan = Plan;

    fn plan(&self, env: &crate::family::Env, width: f64) -> Result<Plan, Diagnostic> {
        plan(self, env.book, width)
    }

    fn draw(&self, plan: Plan, height: f64) -> Result<Vec<Node>, Diagnostic> {
        Ok(draw(self, &plan, height))
    }
}
