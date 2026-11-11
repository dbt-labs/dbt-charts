//! A callout card: an optional title and a message, each markdown, on a bordered rectangle. `plan`
//! decides what a width decides (the blocks and the card's least height), `draw` places the nodes in
//! a slot of a given height.
//!
//! The rules are `chart-renderer/src/families/callout.rs`'s (`callout_md_renderer`, `markdown_block`,
//! `compose`, `draw`), cited at each constant; the markdown renders through `mdsvg`, as production's
//! does, and the card's `<svg class="dbt-chart-callout">` is part of the DOM contract and is emitted
//! here. The resolved style is the caller's ([`CalloutStyle`]) until the spine's total style; a text
//! cut to its line cap, which production clips and warns about, is refused by name.
//!
//! [`CalloutStyle::of`] derives the style from the chart's total `CalloutChartStyle` on the board's total
//! `Style`: a `by_tone` color takes the tone's palette role, and stacks carry the emoji family. The card
//! rectangle is [`failed`](super::failed)'s.

use bind::bound::Callout;
use board_model::palette;
use board_model::style::callout::CalloutChartStyle;
use board_model::style::style::Style;
use diagnostic::{diag, refuse, Diagnostic};
use mdsvg::renderer::SVGRenderer;
use mdsvg::style::Style as MdsvgStyle;
use measure::font_files::{font_path, mono_font_path};
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
    title: Option<Block>,
    message: Block,
    /// The card's least height: its content and padding, at least the minimum card height.
    pub natural_height: f64,
}

/// callout.rs:`callout_md_renderer`. Bold paints from the same variable file at `bold_font_weight`;
/// without that slot mdsvg estimates bold by a width ratio and every run after it lands beside its
/// painted glyphs.
fn markdown_renderer(fonts_dir: &str, style: &CalloutStyle, element: &Element, link_color: &str) -> Result<SVGRenderer, String> {
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
    let regular = FontSlot::new(font_path(fonts_dir, &face.family));
    let bold = FontSlot { weight: css_weight_axis(&style.bold_font_weight)?, ..regular.clone() };
    SVGRenderer::new(md_style, FontSlots { bold: Some(bold), mono: Some(FontSlot::new(mono_font_path(fonts_dir))), ..FontSlots::new(regular) })
}

/// callout.rs:`markdown_block`, with the cap `compose` holds it to: a text taller than its cap is
/// refused, where production clips it and warns.
fn markdown_block(renderer: &SVGRenderer, text: &str, content_width: f64, face: &Face, lines: usize, what: &str) -> Result<Block, Diagnostic> {
    let rendered = renderer.render_content(&mdsvg::parse(text), content_width, 0.0)?;
    if rendered.height > lines as f64 * face.size * LINE_HEIGHT_RATIO {
        return Err(Diagnostic::refused(refuse!("a callout {what} cut to its line cap is not supported")));
    }
    Ok(Block {
        nodes: mdsvg::indented_nodes(&rendered.elements),
        styles: mdsvg::indented_nodes(std::slice::from_ref(&rendered.style)),
        height: rendered.height,
    })
}

/// The card at `width` (floored at the minimum card width): both texts laid out in the content
/// width. `fonts_dir` holds the served faces.
pub fn plan(card: &CalloutCard, fonts_dir: &str, width: f64) -> Result<Plan, Diagnostic> {
    let style = card.style;
    let pad = &style.padding;
    let w = width.max(MIN_WIDTH);
    let content_width = (w - pad.left - pad.right).max(MIN_CONTENT_WIDTH);
    // The link color is the title's ink in both blocks.
    let link = &style.title.face.color;
    let title = match &card.callout.title {
        Some(text) => {
            let renderer = markdown_renderer(fonts_dir, style, &style.title, link)?;
            Some(markdown_block(&renderer, text, content_width, &style.title.face, MAX_TITLE_LINES, "title")?)
        }
        None => None,
    };
    let renderer = markdown_renderer(fonts_dir, style, &style.message, link)?;
    let message = markdown_block(&renderer, &card.callout.message, content_width, &style.message.face, MAX_MESSAGE_LINES, "message")?;
    let title_height = title.as_ref().map_or(0.0, |block| block.height + style.section_gap);
    let natural_height = (pad.top + pad.bottom + title_height + message.height).max(MIN_HEIGHT);
    Ok(Plan { width: w, title, message, natural_height })
}

/// A block's group: its translation and its authored-kind mark.
fn block_nodes(content: Vec<Node>, (x, y): (f64, f64), kind: Option<&str>) -> Node {
    let attrs = match kind {
        Some(kind) => Attrs::new().quick("data-authored-kind", kind),
        None => Attrs::new(),
    };
    Node::group(Role::Chrome, attrs, content).translate(x, y)
}

/// The card as nodes: its root `<svg class="dbt-chart-callout">`, drawn in a slot `height` tall
/// (never less than the plan's natural height).
pub fn draw(card: &CalloutCard, plan: Plan, height: f64) -> Vec<Node> {
    let style = card.style;
    let pad = &style.padding;
    let h = height.max(plan.natural_height);
    let mut children = vec![card_rect(&style.background, &style.border, plan.width, 0.0, h)];
    let mut body = Vec::new();
    let mut current_y = pad.top;
    if let Some(title) = plan.title {
        children.extend(title.styles);
        body.push(block_nodes(title.nodes, (pad.left, current_y + style.title.y_offset), Some("title")));
        current_y += title.height + style.section_gap;
    }
    children.extend(plan.message.styles);
    body.push(block_nodes(plan.message.nodes, (pad.left, current_y + style.message.y_offset), None));
    children.extend(body);
    let extent = Extent { width: plan.width, height: h };
    vec![Node::new(Role::Chrome, Attrs::new().quick("class", "dbt-chart-callout"), Shape::Svg { extent, children })]
}
