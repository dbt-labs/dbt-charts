//! `style/theme/board.py`: frame, title, text and placeholder styles.
//!
//! Dissolved, not ported: `RootFontStyle._coerce_bare_name` and
//! `_RootFontPatchBase._coerce_bare_name` (a bare name or `extends` on the root
//! font is a type error or an unknown key, because [`RootFontStyle`] has no
//! `extends`), `reject_named_root_font`, `font_weight_as_css` and
//! `VALID_FONT_WEIGHTS` (consumers of a resolved weight, not model shape).

use crate::facet::{Color, FontFamily, FontWeightValue};
use crate::primitives::{BorderStyle, DividerStyle, TextBaseline};
use crate::rule::Auto;
use crate::string_enum;
use crate::style::font::{FontSlant, FontStyle, LetterCase, TextDecoration};
use model_core::Engine;
use model_derive::Model;

string_enum!(
    /// How a title or subtitle that overflows its box is handled.
    OverflowMode { Clip = "clip", Truncate = "truncate", WrapTwo = "wrap-two", Wrap = "wrap" }
);

string_enum!(
    /// Alignment of the board body text.
    TextAlign { Left = "left", Center = "center", Right = "right" }
);

string_enum!(
    /// Emoji rendering mode for the board font stack.
    EmojiMode { Monochrome = "monochrome", SystemDefault = "system-default", Disabled = "disabled" }
);

string_enum!(
    /// Pygments' built-in styles as of 2.21, the floor in `pyproject.toml`.
    PygmentsTheme {
        Abap = "abap",
        Algol = "algol",
        AlgolNu = "algol_nu",
        Arduino = "arduino",
        Autumn = "autumn",
        Borland = "borland",
        Bw = "bw",
        Coffee = "coffee",
        Colorful = "colorful",
        Default = "default",
        Dracula = "dracula",
        Emacs = "emacs",
        Friendly = "friendly",
        FriendlyGrayscale = "friendly_grayscale",
        Fruity = "fruity",
        GithubDark = "github-dark",
        GruvboxDark = "gruvbox-dark",
        GruvboxLight = "gruvbox-light",
        Igor = "igor",
        Inkpot = "inkpot",
        Lightbulb = "lightbulb",
        Lilypond = "lilypond",
        Lovelace = "lovelace",
        Manni = "manni",
        Material = "material",
        Monokai = "monokai",
        Murphy = "murphy",
        Native = "native",
        NightOwl = "night-owl",
        Nord = "nord",
        NordDarker = "nord-darker",
        OneDark = "one-dark",
        ParaisoDark = "paraiso-dark",
        ParaisoLight = "paraiso-light",
        Pastie = "pastie",
        Perldoc = "perldoc",
        RainbowDash = "rainbow_dash",
        Rrt = "rrt",
        Sas = "sas",
        SolarizedDark = "solarized-dark",
        SolarizedLight = "solarized-light",
        Staroffice = "staroffice",
        StataDark = "stata-dark",
        StataLight = "stata-light",
        Tango = "tango",
        Trac = "trac",
        Vim = "vim",
        Vs = "vs",
        Xcode = "xcode",
        Zenburn = "zenburn",
    }
);

/// Per-chart padding inset in pixels. The board layer adds `card_padding` to
/// each side, so per-chart padding composes with the card layout.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(complete)]
pub struct PaddingStyle {
    /// Left padding in pixels.
    pub left: f64,
    /// Right padding in pixels.
    pub right: f64,
    /// Top padding in pixels.
    pub top: f64,
    /// Bottom padding in pixels.
    pub bottom: f64,
}

/// Author overrides for the column layout of board body text.
///
/// These narrow the renderer's choice rather than switching columns on.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(max_number, ge = 1), range(max_chars, gt = 0)))]
pub struct TextColumnStyle {
    /// Ceiling on the column count. The renderer may choose fewer when there is
    /// not enough text to fill them.
    #[model(absent = "no ceiling")]
    pub max_number: Option<u32>,
    /// Vertical line drawn between columns.
    pub divider: DividerStyle,
    /// Column text width as a character count, overriding the shipped measure.
    #[model(absent = "the shipped measure")]
    pub max_chars: Option<u32>,
}

/// Root-level font: the cascade floor every other font inherits from, so it
/// takes no `extends` and a named font is refused. Carries a required emoji mode.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(size, gt = 0)))]
pub struct RootFontStyle {
    /// Font family or CSS stack; single-quote multiword names: "'Source Serif 4', serif".
    #[model(fill = "'Inter Variable', Inter, system-ui, sans-serif")]
    pub family: FontFamily,
    /// Text color as a CSS color string.
    pub color: Color,
    /// Font size in pixels.
    #[model(fill = 11.0)]
    pub size: f64,
    /// How heavy the type is drawn (e.g., 'bold', 400, 700).
    #[model(fill = "400")]
    pub weight: FontWeightValue,
    /// Upright or slanted type (normal or italic).
    #[model(fill = "normal")]
    pub style: FontSlant,
    /// Line drawn on the text (underline, line-through, or none).
    #[model(fill = "none")]
    pub decoration: TextDecoration,
    /// Letter-case transform applied at render time.
    #[model(fill = "none")]
    pub case: LetterCase,
    /// Line height as a unitless multiple of font size.
    #[model(fill = 1.25)]
    pub line_height: f64,
    /// Emoji rendering mode for the board font stack.
    pub emoji: EmojiMode,
}

/// Board-level structural dimensions; cascades to child boards.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(max_width, gt = 0)))]
pub struct FrameStyle {
    /// Widest a board without an exact width may grow, in pixels. A board with
    /// no root `width:` hugs its charts' preferred widths up to this bound.
    pub max_width: f64,
    /// Minimum board height in pixels.
    pub min_height: f64,
    /// Board outer margin in pixels.
    pub margin: f64,
    /// Padding added to each card side in pixels.
    pub card_padding: f64,
    /// Gap between cards in pixels.
    pub card_gap: f64,
}

/// Additive level offsets applied to a title's heading level, by card pixel width.
///
/// Added to the base heading level before indexing into `style.fonts.heading.sizes`.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TitleWidthOffsetsStyle {
    /// Level offset for the tiny tier (cards narrower than ~360px).
    pub tiny: i32,
    /// Level offset for the narrow tier (cards ~360-559px).
    pub narrow: i32,
    /// Level offset for the medium tier (cards ~560-1099px).
    pub medium: i32,
    /// Level offset for the wide tier (cards ~1100px and up).
    pub wide: i32,
}

/// Vega-Lite title positioning pass-throughs grouped as a sub-object.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TitlePositionStyle {
    /// Title anchor position; theme always sets this.
    pub anchor: String,
    /// Title rotation angle in degrees; 'auto' lets Vega-Lite choose.
    #[model(fill = "auto")]
    pub angle: Engine<f64, Auto>,
    /// Title offset from its anchor in pixels.
    #[model(absent = "Vega-Lite's default")]
    pub offset: Option<f64>,
    /// Title text baseline alignment; 'auto' uses Vega-Lite's default.
    #[model(fill = "auto")]
    pub baseline: Engine<TextBaseline, Auto>,
}

/// Subtitle font and overflow.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TitleSubtitleStyle {
    /// Subtitle font style overrides (color, family, size, weight).
    #[model(inherit_slot = "Style.font")]
    pub font: FontStyle,
    /// Text overflow mode for the subtitle (clip, truncate, wrap-two, wrap).
    #[model(fill = "wrap-two")]
    pub overflow: OverflowMode,
}

/// A chart title's font: a font whose `size`, `weight` and `family` also take
/// `auto`, the card width tier's choice.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(shorthand = "extends")]
pub struct ChartTitleFontStyle {
    /// Name of a `style.fonts` entry this slot starts from; the fields set beside it override that entry.
    #[model(absent = "no named font")]
    pub extends: Option<String>,
    /// Font family name, or 'auto' to follow the card width.
    pub family: Engine<FontFamily, Auto>,
    /// Text color as a CSS color string.
    pub color: Color,
    /// Font size in pixels, or 'auto' to follow the card width.
    pub size: Engine<f64, Auto>,
    /// How heavy the type is drawn (e.g., 'bold', 400, 700), or 'auto' to follow the card width.
    pub weight: Engine<FontWeightValue, Auto>,
    /// Upright or slanted type (normal or italic).
    pub style: FontSlant,
    /// Line drawn on the text (underline, line-through, or none).
    pub decoration: TextDecoration,
    /// Letter-case transform applied at render time.
    pub case: LetterCase,
    /// Line height as a unitless multiple of font size.
    pub line_height: f64,
}

/// The title fields every title shares; the font differs by title.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(level, ge = 1)))]
pub struct TitleBase {
    /// Object-title font weight on tiny cards.
    pub compact_weight: FontWeightValue,
    /// Additive level offsets by card width (tiny/narrow/medium/wide).
    pub width_offsets: TitleWidthOffsetsStyle,
    /// Minimum title row height in pixels.
    pub min_height: f64,
    /// Text overflow mode (clip, truncate, wrap-two, wrap).
    pub overflow: OverflowMode,
    /// Vega-Lite title positioning: anchor, angle, offset, baseline.
    pub position: TitlePositionStyle,
    /// Heading level override for board titles. 'auto' counts titled ancestors;
    /// an integer locks all titles in this board and its descendants to that H-level.
    pub level: Engine<u32, Auto>,
    /// Subtitle font styles.
    pub subtitle: TitleSubtitleStyle,
}

/// Typography shared by every heading: board, prose, chart, table and spark titles.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TitleStyle {
    #[flatten]
    pub base: TitleBase,
    /// Title font style overrides.
    #[model(inherit_slot = "Style.font")]
    pub font: FontStyle,
}

/// A chart's title style, whose font also takes `auto`.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ChartTitleStyle {
    #[flatten]
    pub base: TitleBase,
    /// Title font style overrides.
    #[model(inherit_slot = "Style.font")]
    pub font: ChartTitleFontStyle,
}

/// Shared boxed-surface group: font, background and border.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct BoxStyle {
    /// Box text font overrides.
    pub font: FontStyle,
    /// Box background fill.
    pub background: Color,
    /// Box border (width, color, radius).
    pub border: BorderStyle,
}

/// Inline and fenced code spans in markdown prose.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TextCodeStyle {
    #[flatten]
    pub boxed: BoxStyle,
    /// Syntax-highlight fenced markdown code blocks.
    pub highlight: bool,
    /// Pygments style name for fenced markdown code tokens.
    pub theme: PygmentsTheme,
}

/// Blockquote prose; the border is the left bar.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TextBlockquoteStyle {
    #[flatten]
    pub boxed: BoxStyle,
}

/// Inline **bold** text runs in markdown prose, distinct from heading weight.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(value_in(
    weight, "normal", "bold", "100", "200", "300", "400", "500", "600", "700", "800", "900", 100,
    200, 300, 400, 500, 600, 700, 800, 900
)))]
pub struct TextBoldStyle {
    /// CSS font-weight for bold text runs and bold table cells.
    pub weight: FontWeightValue,
}

/// Top and bottom margin for a prose block role, in line-height units.
///
/// Adjacent block margins collapse to the larger, CSS-style.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct BlockMarginStyle {
    /// Space above the block, in line-height units.
    pub margin_top: f64,
    /// Space below the block, in line-height units.
    pub margin_bottom: f64,
}

/// Markdown and plain text content.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TextStyle {
    /// Text font style overrides.
    #[model(inherit_slot = "Style.font")]
    pub font: FontStyle,
    /// Text alignment for the board body-text block.
    pub align: TextAlign,
    /// Spacing above/below each paragraph block.
    pub paragraph: BlockMarginStyle,
    /// Heading spacing: margin_top above H1 and H2, margin_bottom below H3 to H6.
    pub heading: BlockMarginStyle,
    /// Multi-column layout for the board body-text block.
    pub column: TextColumnStyle,
    /// Inline and fenced code box styling.
    pub code: TextCodeStyle,
    /// Blockquote box styling.
    pub blockquote: TextBlockquoteStyle,
    /// Inline bold-run styling, distinct from heading weight.
    pub bold: TextBoldStyle,
    /// Markdown horizontal rules (`---`) and markdown-table gridlines.
    pub divider: DividerStyle,
}

/// Placeholder overlay shown on empty charts.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PlaceholderOverlay {
    /// Placeholder overlay text; `auto` takes the board language's wording.
    pub text: String,
    /// Overlay background color.
    pub background: Color,
    /// Overlay font style overrides.
    #[model(inherit_slot = "Style.font")]
    pub font: FontStyle,
}

/// The placeholder drawn over a chart with no data.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PlaceholderStyle {
    /// Opacity of the placeholder overlay (0-1).
    pub opacity: f64,
    /// Overlay text and background style.
    pub overlay: PlaceholderOverlay,
}
