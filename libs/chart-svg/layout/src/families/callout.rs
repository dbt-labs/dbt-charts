//! A callout card: an optional title and a message, each markdown, on a bordered rectangle. `plan`
//! decides what a width decides (the blocks and the card's least height), `draw` places the nodes in
//! a slot of a given height.
//!
//! The rules are `chart-renderer/src/families/callout.rs`'s (`callout_md_renderer`, `markdown_block`,
//! `compose`, `draw`), cited at each constant; the markdown renders through `mdsvg`, as production's
//! does, and the card's `<svg class="dbt-chart-callout">` is part of the DOM contract and is emitted
//! here. The resolved style is the caller's ([`CalloutStyle`]) until the spine's total style. A text
//! taller than its line cap is clipped to it and warns, as production's `draw` does.
//!
//! [`CalloutStyle::of`] derives the style from the chart's total `CalloutChartStyle` on the board's total
//! `Style`: a `by_tone` color takes the tone's palette role, and stacks carry the emoji family. The card
//! rectangle is [`failed`](super::failed)'s.

use bind::bound::Callout;
use board_model::palette;
use board_model::style::callout::CalloutChartStyle;
use board_model::style::style::Style;
use diagnostic::{diag, refuse, Diagnostic};
use crate::legacy;
use mdsvg::renderer::SVGRenderer;
use mdsvg::style::Style as MdsvgStyle;
use measure::font_files::{font_file, MONO_FILE};
use measure::shaped::FontBook;
use measure::fonts::{css_weight_axis, FontSlot, FontSlots};
use scene::{Attrs, Extent, Node, Role, Shape};

use super::failed::{card_rect, code_family, Border, Face, Padding, Weight, LINE_HEIGHT_RATIO, MAX_MESSAGE_LINES, MIN_HEIGHT, MIN_WIDTH};

/// callout.rs:49. A title is cut to this many lines.
const MAX_TITLE_LINES: usize = 3;
/// callout.rs:`compose`. The least width the text may wrap in.
const MIN_CONTENT_WIDTH: f64 = 40.0;

/// A title or message: its face and where it sits from its anchor.
#[derive(Debug, Clone, PartialEq)]
pub struct Element {
    pub face: Face,
    pub y_offset: f64,
}

/// The resolved callout style (`ResolvedCalloutStyle`), less what the slot reads (`preferred_width`)
/// and what paints nothing (`tone`: its colors are resolved in [`CalloutStyle::of`]).
#[derive(Debug, Clone, PartialEq)]
pub struct CalloutStyle {
    /// Paints nothing: it keys the clip ids' hash, as production's `callout_hash` does.
    pub tone: String,
    pub background: String,
    pub border: Border,
    pub padding: Padding,
    pub section_gap: f64,
    pub bold_font_weight: String,
    pub code_family: String,
    pub title: Element,
    pub message: Element,
}

/// `colors.BY_TONE`: a color the cascade left for the tone's palette role to fill.
const BY_TONE: &str = "by_tone";

impl CalloutStyle {
    /// The style a callout draws with: `chart`, its effective total style, on the board scope's total
    /// `style` (`build_resolved_callout_chart`). Each color left as `by_tone` takes its tone's
    /// `{tone}.{role}` palette color (bg, border, solid, text); any other is the author's, which the
    /// style cascade has already resolved (`palette::hook::resolve`: a palette token or a bare theme
    /// role becomes its color, a hex or CSS name stays). A word the cascade left that names no color
    /// is refused by name rather than printed into the SVG; a token it left unresolved is a bug in
    /// the style the caller passed (a chart's own tokens are resolved in `CompiledBoard.placed`). Every font
    /// stack carries the board's emoji family.
    pub fn of(chart: &CalloutChartStyle, style: &Style) -> Result<Self, Diagnostic> {
        let tone = chart.tone.as_str();
        let color = |value: &str, role: &str| {
            if value == BY_TONE {
                let token = format!("{tone}.{role}");
                return palette::color(&token).map_err(|e| diag::err_internal(format!("the callout's `{token}` color does not resolve: {e}")));
            }
            if dbt_charts_color::is_color_token(value) {
                return Err(diag::err_internal(format!("the callout color {value} is a token the cascade left unresolved")));
            }
            if dbt_charts_color::is_unknown_color_name(value) {
                return Err(Diagnostic::refused(refuse!("a callout color that is not a resolved color is not supported")));
            }
            Ok(value.to_string())
        };
        let element = |element: &board_model::style::callout::CalloutElementStyle, role: &str| {
            let font = &element.font;
            let ink = color(font.color.as_str(), role)?;
            Ok::<_, Diagnostic>(Element { face: Face::of(font.family.as_str(), font.size, &font.weight, &ink, &style.font.emoji), y_offset: element.y_offset })
        };
        let border = &chart.border;
        let pad = &chart.padding;
        Ok(Self {
            tone: tone.to_string(),
            background: color(chart.background.as_str(), "bg")?,
            border: Border {
                width: border.width,
                color: color(border.color.as_str(), "border")?,
                radius: border.radius,
                dash_array: border.dash_array.clone(),
                line_cap: border.line_cap.as_ref().map(|cap| cap.as_str().to_string()),
                dash_offset: border.dash_offset,
            },
            padding: Padding { top: pad.top, right: pad.right, bottom: pad.bottom, left: pad.left },
            section_gap: chart.section_gap,
            bold_font_weight: Weight::of(&style.text.bold.weight.0).attr(),
            code_family: code_family(style)?,
            title: element(&chart.title, "solid")?,
            message: element(&chart.message, "text")?,
        })
    }
}

#[derive(Debug, Clone, Copy)]
pub struct CalloutCard<'a> {
    pub callout: &'a Callout,
    pub style: &'a CalloutStyle,
    /// The chart's public id, which a truncation warning names.
    pub id: &'a str,
    /// Each project image its text writes, by that reference, as the `data:` URI its host read.
    pub images: &'a std::collections::BTreeMap<String, String>,
    /// The markdown rules already written into the `<svg>` this card draws in; a card with no
    /// enclosing scope of its own prints every rule it needs.
    pub scope: Option<&'a std::cell::RefCell<mdsvg::StyleScope>>,
}

/// A title or message laid out: the nodes it draws at its own origin, the `<style>` its markdown
/// needs, and the height it asks for.
#[derive(Debug, Clone)]
struct Block {
    nodes: Vec<Node>,
    styles: Vec<Node>,
    height: f64,
}

/// What a width decides, shared by the least height and the drawing.
#[derive(Debug, Clone)]
pub struct Plan {
    width: f64,
    content_width: f64,
    /// Each block with the height it is drawn at: its own, or its line cap when taller.
    title: Option<(Block, f64)>,
    message: (Block, f64),
    /// The card's least height: its content and padding, at least the minimum card height.
    pub natural_height: f64,
    /// The texts production hashes its clip ids over: the title and message after their project
    /// images resolve.
    title_text: Option<String>,
    message_text: String,
    /// A `WARN-CALLOUT-TEXT-TRUNCATED` per text cut to its line cap, the title's first.
    pub diagnostics: Vec<Diagnostic>,
}

/// callout.rs:`callout_md_renderer`. Bold paints from the same variable file at `bold_font_weight`;
/// without that slot mdsvg estimates bold by a width ratio and every run after it lands beside its
/// painted glyphs.
fn markdown_renderer(book: &FontBook, style: &CalloutStyle, element: &Element, link_color: &str) -> Result<SVGRenderer, Diagnostic> {
    let face = &element.face;
    let weight = face.weight.attr();
    // `None if str(coerced_fw).lower() in {"400", "normal"} else coerced_fw`
    let font_weight = if matches!(weight.to_lowercase().as_str(), "400" | "normal") { None } else { Some(weight) };
    let md_style = MdsvgStyle {
        font_family: face.family.clone(),
        base_font_size: face.size,
        font_weight,
        bold_font_weight: style.bold_font_weight.clone(),
        line_height: LINE_HEIGHT_RATIO,
        text_color: face.color.clone(),
        heading_color: Some(face.color.clone()),
        link_color: link_color.to_string(),
        link_underline: true,
        code_font_family: style.code_family.clone(),
        code_color: face.color.clone(),
        code_background: "rgba(0,0,0,0.07)".to_string(),
        paragraph_spacing: face.size * 0.5,
        list_item_spacing: 2.0,
        ..Default::default()
    };
    let regular = FontSlot::of(book.file(font_file(&face.family))?);
    let bold = FontSlot { weight: css_weight_axis(&style.bold_font_weight)?, ..regular.clone() };
    let mono = FontSlot::of(book.file(MONO_FILE)?);
    Ok(SVGRenderer::new(md_style, FontSlots { bold: Some(bold), mono: Some(mono), ..FontSlots::new(regular) })
        .map_err(crate::markdown_style::mdsvg_error)?
        .with_dash_fit(|dashes, length| crate::dash::fit_pattern(dashes, length, false)))
}

/// callout.rs:`markdown_block`, over text whose project images `resolved_text` already rewrote.
fn markdown_block(renderer: &SVGRenderer, text: &str, content_width: f64, scope: &mut mdsvg::StyleScope) -> Result<Block, Diagnostic> {
    let rendered = renderer.render_content(&mdsvg::parse(text), content_width, 0.0).map_err(crate::markdown_style::mdsvg_error)?;
    Ok(Block {
        nodes: mdsvg::indented_nodes(&rendered.elements),
        styles: mdsvg::indented_nodes(scope.style(&rendered.rules).as_slice()),
        height: rendered.height,
    })
}

/// `image_assets.rewrite_markdown_text_images`: `text` with each project image's destination
/// replaced by the `data:` URI its host supplies. Production hashes, warns and parses this text,
/// so the engine does too. A project image the host does not supply is refused by name.
fn resolved_text(text: &str, images: &std::collections::BTreeMap<String, String>) -> Result<String, Diagnostic> {
    mdsvg::parser::rewrite_text_images(text, |url| {
        if !mdsvg::types::is_local_image(url) {
            return Ok(url.to_string());
        }
        images.get(url).cloned().ok_or_else(|| Diagnostic::refused(refuse!("a callout image read from the project is not supported")))
    })
    .map_err(|err| match err {
        mdsvg::parser::TextImageError::Resolve(refusal) => refusal,
        mdsvg::parser::TextImageError::Reshaped => Diagnostic::refused(refuse!("a callout whose image syntax cannot be rewritten without changing other markdown is not supported")),
    })
}

/// callout.rs:`compose`: `block` held to `lines` of `face`, with the warning a cut text earns.
fn capped(block: Block, face: &Face, lines: usize, id: &str, field: &str, text: &str, diagnostics: &mut Vec<Diagnostic>) -> (Block, f64) {
    let cap = lines as f64 * face.size * LINE_HEIGHT_RATIO;
    if block.height > cap {
        diagnostics.push(diag::warn_callout_text_truncated(id, field, text));
    }
    let height = block.height.min(cap);
    (block, height)
}

/// The card at `width` (floored at the minimum card width): both texts laid out in the content
/// width. `book` holds the served faces.
pub fn plan(card: &CalloutCard, book: &FontBook, width: f64) -> Result<Plan, Diagnostic> {
    let style = card.style;
    let pad = &style.padding;
    let w = width.max(MIN_WIDTH);
    let content_width = (w - pad.left - pad.right).max(MIN_CONTENT_WIDTH);
    // The link color is the title's ink in both blocks.
    let link = &style.title.face.color;
    let mut diagnostics = Vec::new();
    let own = std::cell::RefCell::new(mdsvg::StyleScope::default());
    let scope = card.scope.unwrap_or(&own);
    let title_text = card.callout.title.as_deref().map(|text| resolved_text(text, card.images)).transpose()?;
    let title = match &title_text {
        Some(text) => {
            let renderer = markdown_renderer(book, style, &style.title, link)?;
            let block = markdown_block(&renderer, text, content_width, &mut scope.borrow_mut())?;
            Some(capped(block, &style.title.face, MAX_TITLE_LINES, card.id, "title", text, &mut diagnostics))
        }
        None => None,
    };
    let message_text = resolved_text(&card.callout.message, card.images)?;
    let renderer = markdown_renderer(book, style, &style.message, link)?;
    let block = markdown_block(&renderer, &message_text, content_width, &mut scope.borrow_mut())?;
    let message = capped(block, &style.message.face, MAX_MESSAGE_LINES, card.id, "message", &message_text, &mut diagnostics);
    let natural_height = content_height(pad, title.as_ref().map(|(_, height)| *height), style.section_gap, message.1).max(MIN_HEIGHT);
    Ok(Plan { width: w, content_width, title, message, natural_height, title_text, message_text, diagnostics })
}

/// callout.rs:`compose`'s `natural_height` before its floor: the padding, the drawn title and its
/// gap, and the drawn message, summed left to right as production sums them.
fn content_height(pad: &Padding, title: Option<f64>, section_gap: f64, message: f64) -> f64 {
    let (title_height, title_gap) = title.map_or((0.0, 0.0), |height| (height, section_gap));
    pad.top + pad.bottom + title_height + title_gap + message
}

/// callout.rs:541 (`callout_hash`): the clip ids' hash, an md5 over the callout's inputs.
fn clip_hash(card: &CalloutCard, plan: &Plan) -> String {
    let width = plan.width;
    let style = card.style;
    let number = legacy::hashed_number;
    legacy::callout_hash(&[
        plan.title_text.as_deref().unwrap_or(""),
        plan.message_text.as_str(),
        style.tone.as_str(),
        &number(width),
        &number(style.padding.left),
        &number(style.padding.right),
        &number(style.padding.top),
        &number(style.padding.bottom),
        &number(style.section_gap),
        style.title.face.family.as_str(),
        &number(style.title.face.size),
        &style.title.face.weight.hashed(),
        style.message.face.family.as_str(),
        &number(style.message.face.size),
        &style.message.face.weight.hashed(),
        style.title.face.color.as_str(),
        style.message.face.color.as_str(),
    ])
}

/// callout.rs:`block_nodes`: a block's group, its translation, its clip (`id` and size) when
/// the block is cut to its cap, and its authored-kind mark.
fn block_nodes(content: Vec<Node>, (x, y): (f64, f64), clip: Option<(String, f64, f64)>, kind: Option<&str>) -> Vec<Node> {
    let mut nodes = Vec::new();
    let mut attrs = Attrs::new();
    if let Some((id, width, height)) = clip {
        let rect = Node::rect(Role::Chrome, (0.0, 0.0, width, height), Attrs::new());
        let clip_path = Node::new(Role::Chrome, Attrs::new().quick("id", &id), Shape::ClipPath(vec![rect]));
        nodes.push(Node::new(Role::Chrome, Attrs::new(), Shape::Defs(vec![clip_path])));
        attrs = attrs.quick("clip-path", format!("url(#{id})"));
    }
    if let Some(kind) = kind {
        attrs = attrs.quick("data-authored-kind", kind);
    }
    nodes.push(Node::group(Role::Chrome, attrs, content).translate(x, y));
    nodes
}

/// The card as nodes: its root `<svg class="dbt-chart-callout">`, drawn in a slot `height` tall
/// (never less than the plan's natural height).
pub fn draw(card: &CalloutCard, plan: Plan, height: f64) -> Vec<Node> {
    let style = card.style;
    let pad = &style.padding;
    let h = height.max(plan.natural_height);
    let hash = clip_hash(card, &plan);
    let content_width = plan.content_width;
    let clip = |prefix: &str, block: &Block, drawn: f64| (block.height > drawn).then(|| (format!("{prefix}-clip-{hash}"), content_width, drawn));
    let mut children = vec![card_rect(&style.background, &style.border, plan.width, 0.0, h)];
    let mut body = Vec::new();
    let mut current_y = pad.top;
    if let Some((title, drawn)) = plan.title {
        let clipped = clip("title", &title, drawn);
        children.extend(title.styles);
        body.extend(block_nodes(title.nodes, (pad.left, current_y + style.title.y_offset), clipped, Some("title")));
        current_y += drawn + style.section_gap;
    }
    let (message, drawn) = plan.message;
    let clipped = clip("msg", &message, drawn);
    children.extend(message.styles);
    body.extend(block_nodes(message.nodes, (pad.left, current_y + style.message.y_offset), clipped, None));
    children.extend(body);
    let extent = Extent { width: plan.width, height: h };
    vec![Node::new(Role::Chrome, Attrs::new().quick("class", "dbt-chart-callout"), Shape::Svg { extent, children })]
}

impl crate::family::Family for CalloutCard<'_> {
    type Plan = Plan;

    fn plan(&self, env: &crate::family::Env, width: f64) -> Result<Plan, Diagnostic> {
        plan(self, env.book, width)
    }

    fn draw(&self, plan: Plan, height: f64) -> Result<Vec<Node>, Diagnostic> {
        Ok(draw(self, plan, height))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_content_height_sums_in_production_s_order() {
        let (top, bottom, title, gap, message) = (12.047730434552244, 27.538651292631567, 48.25813012527192, 16.00904702991617, 96.55198851922388);
        // chart-renderer/src/families/callout.rs's `compose` sums these left to right; these
        // values tell that order from a title grouped with its gap.
        assert_ne!(top + bottom + title + gap + message, top + bottom + (title + gap) + message);
        let pad = Padding { top, bottom, left: 0.0, right: 0.0 };
        assert_eq!(content_height(&pad, Some(title), gap, message), top + bottom + title + gap + message);
        assert_eq!(content_height(&pad, None, gap, message), top + bottom + 0.0 + 0.0 + message);
    }
}
