//! The theme's markdown style and the faces board prose and titles draw in: the port of
//! `compile/resolve/style/markdown_style.py` (`compact_style_kwargs`, `title_renderer`,
//! `prose_measure`, `max_chars_to_px`, `readable_measure`) and `typography.py::board_title_spec`.
//! `chart-renderer/src/prose.rs` (`prose_char_ratio`, `readable_measure`) and `title.rs` hold the
//! same laws for the old composer; the cutover deletes those copies.
//!
//! Compile-time sizing measures prose and titles with this style and render paints with it, so both
//! ask here. A word the theme sets that mdsvg paints differently from production (a code or
//! blockquote font beyond its family and color, an upper or lower case) is refused by name.

use bind::font_stack::with_emoji;
use board_model::style::board::{EmojiMode, TextAlign};
use board_model::style::font::LetterCase;
use board_model::style::style::Style;
use diagnostic::{diag, refuse, Diagnostic};
use mdsvg::style::{Style as MdsvgStyle, TextAlign as MdAlign};
use measure::font_files::{font_path, mono_font_path};
use measure::fonts::{cached_measurer, css_weight_axis, FontSlot, FontSlots};
use model_core::Engine;

use crate::families::failed::{code_family, Weight};

/// `_PROSE_SAMPLE`: one sentence of ordinary English prose that calibrates a face's character width.
const PROSE_SAMPLE: &str = "North America's primary issue is insufficient MQL supply, not a regional Stage 0 target miss or a broad loss of selling capacity.";

/// `MAX_CHARS`: the readable line length when the theme sets none.
const MAX_CHARS: u32 = 75;

/// What a caller overrides of `get_compact_style`.
#[derive(Debug, Clone)]
pub struct Overrides {
    pub font_family: Option<String>,
    pub h1_size: Option<f64>,
    pub heading_weight: Option<String>,
}

impl Overrides {
    /// Body prose: the theme's own sizes, family and heading weight.
    pub const PROSE: Overrides = Overrides {
        font_family: None,
        h1_size: None,
        heading_weight: None,
    };
}

fn align(style: &Style) -> MdAlign {
    match style.text.align {
        TextAlign::Left => MdAlign::Left,
        TextAlign::Center => MdAlign::Center,
        TextAlign::Right => MdAlign::Right,
    }
}

fn heading_ramp<'a>(style: &'a Style, name: &str) -> Result<&'a [f64], Diagnostic> {
    style
        .fonts
        .get(name)
        .and_then(|font| font.sizes.as_deref())
        .ok_or_else(|| {
            diag::err_internal(format!(
                "style.fonts.{name}.sizes is unset in the board's style"
            ))
        })
}

/// A letter case mdsvg cannot paint is refused where the text is, not guessed.
pub fn refuse_case(case: &LetterCase, what: &str) -> Result<(), Diagnostic> {
    if *case == LetterCase::None {
        return Ok(());
    }
    Err(Diagnostic::refused(refuse!(
        "a {what} with a letter case is not supported"
    )))
}

/// A box font's axes as mdsvg takes them: a field the theme leaves unset is the empty string (or 0
/// for the size), which mdsvg reads as the surrounding text's.
struct Axes {
    family: String,
    weight: String,
    style: String,
    size: f64,
    decoration: String,
    case: String,
}

impl Axes {
    /// `emoji` puts the board's emoji family in a family the theme sets; the code family is the
    /// caller's (`code_family`), so its own axis is not read.
    fn of(
        font: &Option<model_core::Patch<board_model::style::font::FontStyle>>,
        scope: &str,
        emoji: Option<&EmojiMode>,
    ) -> Result<Self, Diagnostic> {
        let Some(font) = font else {
            return Ok(Axes {
                family: String::new(),
                weight: String::new(),
                style: String::new(),
                size: 0.0,
                decoration: String::new(),
                case: String::new(),
            });
        };
        // `_mdsvg_case`: mdsvg paints upper and lower only.
        let case = match font.case.as_ref() {
            None | Some(LetterCase::None) => String::new(),
            Some(LetterCase::Upper) => "upper".to_string(),
            Some(LetterCase::Lower) => "lower".to_string(),
            Some(_) => {
                return Err(Diagnostic::refused(refuse!(
                    "a {scope} font case other than upper or lower is not supported"
                )))
            }
        };
        Ok(Axes {
            family: match (&font.family, emoji) {
                (Some(family), Some(emoji)) => with_emoji(family.as_str(), emoji),
                _ => String::new(),
            },
            weight: font
                .weight
                .as_ref()
                .map_or(String::new(), |w| Weight::of(w).attr()),
            style: font
                .style
                .as_ref()
                .map_or(String::new(), |v| v.as_str().to_string()),
            size: font.size.unwrap_or(0.0),
            decoration: font
                .decoration
                .as_ref()
                .map_or(String::new(), |v| v.as_str().to_string()),
            case,
        })
    }
}

/// `compact_style_kwargs`: the mdsvg style the resolved theme draws markdown with.
pub fn compact_style(style: &Style, overrides: &Overrides) -> Result<MdsvgStyle, Diagnostic> {
    let text = &style.text;
    let (body_size, body_line) = (text.font.size, text.font.line_height);
    // A heading margin of 1.5 is a line and a half of the body text the heading belongs to.
    let margin_unit = body_size * body_line;
    let emoji = &style.font.emoji;
    let heading_sizes = heading_ramp(style, "heading")?;
    if heading_sizes.len() != 6 {
        return Err(diag::err_internal(format!(
            "style.fonts.heading.sizes must have 6 entries (H1-H6); got {}",
            heading_sizes.len()
        )));
    }
    // Prose headings read H1 from the H ramp, H2 and H3 from the section ramp, and H4-H6 are body
    // size; none is smaller than the body it heads.
    let section = heading_ramp(style, "section")?;
    let [s2, s3, ..] = section else {
        return Err(diag::err_internal(
            "style.fonts.section.sizes must have at least 2 entries",
        ));
    };
    let sizes = [
        heading_sizes[0].max(body_size),
        s2.max(body_size),
        s3.max(body_size),
        body_size,
        body_size,
        body_size,
    ];
    let paragraph = &text.paragraph;
    let paragraph_gap = body_size * body_line * paragraph.margin_top.max(paragraph.margin_bottom);
    let heading_weight = overrides
        .heading_weight
        .clone()
        .unwrap_or_else(|| Weight::of(&style.title.font.weight).attr());
    let body_weight = Weight::of(&text.font.weight).attr();
    let font_weight =
        (!matches!(body_weight.to_lowercase().as_str(), "400" | "normal")).then_some(body_weight);
    let code = &style.text.code.boxed;
    let quote = &style.text.blockquote.boxed;
    let ink = |font: &Option<model_core::Patch<board_model::style::font::FontStyle>>| {
        font.as_ref().and_then(|f| f.color.as_ref()).map_or_else(
            || style.font.color.as_str().to_string(),
            |c| c.as_str().to_string(),
        )
    };
    let code_axes = Axes::of(&code.font, "code", None)?;
    let quote_axes = Axes::of(&quote.font, "blockquote", Some(emoji))?;
    let divider = &text.divider;
    let table_header = style
        .charts
        .table
        .header
        .background
        .as_ref()
        .unwrap_or(&style.background);
    Ok(MdsvgStyle {
        font_family: with_emoji(
            overrides
                .font_family
                .as_deref()
                .unwrap_or(text.font.family.as_str()),
            emoji,
        ),
        heading_font_family: with_emoji(style.title.font.family.as_str(), emoji),
        base_font_size: body_size,
        font_weight,
        line_height: body_line,
        heading_margin_top_px: Some(margin_unit * text.heading.margin_top),
        heading_margin_bottom_px: Some(margin_unit * text.heading.margin_bottom),
        minor_heading_margin_top_px: Some(paragraph_gap),
        major_heading_margin_bottom_px: Some(paragraph_gap),
        heading_grid_px: Some(body_size * body_line),
        paragraph_spacing: paragraph_gap,
        text_color: text.font.color.as_str().to_string(),
        heading_color: Some(style.title.font.color.as_str().to_string()),
        link_color: style.accent.as_str().to_string(),
        code_color: ink(&code.font),
        code_background: code.background.as_str().to_string(),
        code_block_border_color: code.border.color.as_str().to_string(),
        code_block_border_width: code.border.width,
        code_block_border_radius: code.border.radius,
        code_highlight: style.text.code.highlight,
        code_theme: style.text.code.theme.as_str().to_string(),
        code_font_family: code_family(style)?,
        code_font_weight: code_axes.weight,
        code_font_style: code_axes.style,
        code_font_size: code_axes.size,
        code_font_decoration: code_axes.decoration,
        code_font_case: code_axes.case,
        blockquote_font_family: quote_axes.family,
        blockquote_font_weight: quote_axes.weight,
        blockquote_font_style: quote_axes.style,
        blockquote_font_size: quote_axes.size,
        blockquote_font_decoration: quote_axes.decoration,
        blockquote_font_case: quote_axes.case,
        blockquote_color: ink(&quote.font),
        blockquote_border_color: quote.border.color.as_str().to_string(),
        blockquote_border_width: quote.border.width,
        blockquote_border_radius: quote.border.radius,
        blockquote_background: quote.background.as_str().to_string(),
        // Half a body line on every side: a one-line row pitches one whole line.
        table_cell_padding: body_size * body_line / 2.0,
        table_border_color: divider.color.as_str().to_string(),
        table_border_width: divider.width,
        table_border_dasharray: divider
            .dasharray
            .as_ref()
            .map_or(String::new(), |d| d.as_str().to_string()),
        table_border_visible: divider.visible,
        table_header_background: table_header.as_str().to_string(),
        hr_color: divider.color.as_str().to_string(),
        hr_height: divider.width,
        hr_dasharray: divider
            .dasharray
            .as_ref()
            .map_or(String::new(), |d| d.as_str().to_string()),
        hr_visible: divider.visible,
        text_align: align(style),
        image_preserve_aspect_ratio: "xMidYMin meet".to_string(),
        h1_size: Some(overrides.h1_size.unwrap_or(sizes[0])),
        h2_size: Some(sizes[1]),
        h3_size: Some(sizes[2]),
        h4_size: Some(sizes[3]),
        h5_size: Some(sizes[4]),
        h6_size: Some(sizes[5]),
        heading_font_weight: heading_weight,
        heading_line_height: Some(style.title.font.line_height),
        bold_font_weight: Weight::of(&text.bold.weight).attr(),
        avoid_runts: true,
        ..MdsvgStyle::default()
    })
}

/// `markdown_font_faces`: the files each run of `style` is measured against, instanced at the weight
/// it paints at. Italic runs are not modeled (`has_italic` refuses them).
pub fn font_slots(fonts_dir: &str, style: &MdsvgStyle) -> Result<FontSlots, Diagnostic> {
    let slot = |family: &str, weight: &str| {
        Ok::<_, Diagnostic>(FontSlot {
            weight: css_weight_axis(weight).map_err(diag::err_internal)?,
            ..FontSlot::new(font_path(fonts_dir, family))
        })
    };
    let regular = slot(
        &style.font_family,
        style.font_weight.as_deref().unwrap_or("400"),
    )?;
    let heading_family = if style.heading_font_family.is_empty() {
        &style.font_family
    } else {
        &style.heading_font_family
    };
    Ok(FontSlots {
        bold: Some(slot(&style.font_family, &style.bold_font_weight)?),
        heading: Some(slot(heading_family, &style.heading_font_weight)?),
        mono: Some(FontSlot::new(mono_font_path(fonts_dir))),
        ..FontSlots::new(regular)
    })
}

/// `board_title_spec` for the root title (level 1): the H1 size of the ramp and the title weight.
/// A deeper title reads the section ramp, which no board drawn here has.
pub fn root_title_overrides(style: &Style) -> Result<Overrides, Diagnostic> {
    if let Engine::Value(_) = style.title.base.level {
        return Err(Diagnostic::refused(refuse!(
            "a board title with an authored `style.title.level` is not supported"
        )));
    }
    let sizes = heading_ramp(style, "heading")?;
    Ok(Overrides {
        // `int(sizes[0])`
        h1_size: Some(sizes[0].trunc()),
        heading_weight: Some(Weight::of(&style.title.font.weight).attr()),
        // A title is always a heading: it draws in the title family, never the body's.
        font_family: Some(style.title.font.family.as_str().to_string()),
    })
}

/// `_prose_char_ratio`: the mean width of real prose in the body face, as a fraction of its size.
pub fn char_ratio(fonts_dir: &str, style: &MdsvgStyle) -> Result<f64, Diagnostic> {
    let slots = font_slots(fonts_dir, style)?;
    let measurer = cached_measurer(
        &slots.regular.path,
        slots.regular.font_number,
        slots.regular.weight,
    );
    Ok(measurer.measure(PROSE_SAMPLE, 1.0) / PROSE_SAMPLE.chars().count() as f64)
}

/// `readable_measure`: `max_chars` (or the default) characters of `char_px` each.
pub fn readable_measure(char_px: f64, max_chars: Option<u32>) -> f64 {
    f64::from(max_chars.unwrap_or(MAX_CHARS)) * char_px
}
