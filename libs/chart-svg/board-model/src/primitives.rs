//! The `models/primitives.py` models the theme files embed, other than the font
//! slot (`style::font`): strokes, borders and their closed vocabularies.

use crate::facet::{facet, BorderColor, Color, Facet, FormatName, Palette, SchemeName, Url};
use crate::rule::Auto;
use crate::string_enum;
use crate::unit::Px;
use model_core::{Cx, Engine, FromNode, Leaf, Merge, Node, ReadScalar, Scalar, TypeExpr};
use model_derive::Model;
use regex::Regex;
use std::sync::OnceLock;

string_enum!(
    /// Line interpolation: a curated subset of Vega-Lite's `interpolate`.
    Curve {
        Linear = "linear",
        Monotone = "monotone",
        Natural = "natural",
        Basis = "basis",
        Cardinal = "cardinal",
        Step = "step",
        StepBefore = "step-before",
        StepAfter = "step-after",
    }
);
string_enum!(
    /// How a stroke's ends are finished.
    LineCap { Butt = "butt", Round = "round", Square = "square" }
);
string_enum!(
    /// How two stroke segments are joined.
    LineJoin { Miter = "miter", Round = "round", Bevel = "bevel" }
);

string_enum!(
    /// Semantic tone for palette-role lookup.
    Tone { Positive = "positive", Negative = "negative", Warning = "warning", Info = "info" }
);

string_enum!(
    /// Where a text anchor sits against its text.
    TextBaseline {
        Top = "top",
        Middle = "middle",
        Bottom = "bottom",
        Alphabetic = "alphabetic",
        LineTop = "line-top",
        LineBottom = "line-bottom",
    }
);

/// Stroke appearance shared across mark families.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct StrokeStyle {
    /// Stroke color as a CSS color string.
    pub color: Engine<Color, Auto>,
    /// Stroke width in pixels.
    pub width: Engine<Px, Auto>,
    /// How the stroke's ends are finished (butt, round, or square).
    pub cap: Engine<LineCap, Auto>,
    /// How two stroke segments are joined (miter, round, or bevel).
    pub join: Engine<LineJoin, Auto>,
    /// Lengths of the dashes and the blanks between them (e.g. '4 2').
    #[model(absent = "solid line")]
    pub dasharray: Option<String>,
}

/// Stroke for rule marks whose color defaults to the root font color.
///
/// Its own model, not a flatten of `StrokeStyle`: `color` differs (it inherits,
/// and takes no `auto`).
#[derive(Debug, Clone, PartialEq, Model)]
pub struct FontColorStrokeStyle {
    /// Stroke color for rule marks.
    #[model(inherit = "Style.font.color")]
    pub color: Color,
    /// Stroke width in pixels.
    #[model(fill = 1.0)]
    pub width: Engine<Px, Auto>,
    /// How the stroke's ends are finished (butt, round, or square).
    #[model(fill = "auto")]
    pub cap: Engine<LineCap, Auto>,
    /// How two stroke segments are joined (miter, round, or bevel).
    #[model(fill = "auto")]
    pub join: Engine<LineJoin, Auto>,
    /// Lengths of the dashes and the blanks between them (e.g. '4 2').
    #[model(absent = "solid line")]
    pub dasharray: Option<String>,
}

/// Box border. Does not cascade: box properties reset per level. A bare string is the CSS
/// shorthand `2px dashed #333`.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(shorthand_with = border_css, string_pattern = BORDER_STRING_PATTERN)]
pub struct BorderStyle {
    /// Border width in pixels.
    pub width: f64,
    /// Border color as a CSS color string.
    pub color: BorderColor,
    /// Border corner radius in pixels.
    pub radius: f64,
    /// SVG stroke-dasharray pattern in pixels (e.g. [4, 4]).
    #[model(absent = "solid border")]
    pub dash_array: Option<Vec<f64>>,
    /// How each dash's ends are finished on a dashed border.
    #[model(absent = "butt ends")]
    pub line_cap: Option<LineCap>,
    /// How far into the dash pattern the line starts, in pixels.
    #[model(absent = "the pattern starts at 0")]
    pub dash_offset: Option<f64>,
}

/// Pydantic's `Dasharray` pattern, whose `\d` and `\s` are Unicode.
fn dasharray(v: &str) -> Result<String, String> {
    static PATTERN: OnceLock<Option<Regex>> = OnceLock::new();
    let pattern = PATTERN
        .get_or_init(|| Regex::new(r"^\s*\d+(\.\d+)?([ ,]+\d+(\.\d+)?)*\s*$").ok())
        .as_ref();
    if pattern.is_some_and(|p| p.is_match(v)) {
        // Authored spelling is kept; `Dasharray::normalized` is the stored form.
        return Ok(v.to_string());
    }
    Err(format!(
        "dasharray must be positive numbers such as '6 3', got {v}"
    ))
}

/// A dash pattern: positive numbers separated by spaces or commas.
const DASHARRAY_PATTERN: &str = r"^\s*\d+(\.\d+)?([ ,]+\d+(\.\d+)?)*\s*$";

/// The same under an `Engine` taking `auto`: one pattern states both spellings.
pub(crate) const DASHARRAY_PATTERN_WITH_AUTO: &str =
    r"^(auto|\s*\d+(\.\d+)?([ ,]+\d+(\.\d+)?)*\s*)$";

facet!(
    /// A dash pattern in pixels such as `6 3`, space separated once normalized.
    Dasharray = "dasharray",
    dasharray,
    model_core::Keywords::pattern(DASHARRAY_PATTERN)
        .in_engine(model_core::InEngine::Pattern(DASHARRAY_PATTERN_WITH_AUTO))
);

impl Dasharray {
    /// The compiled form: single-space separated, as production stores it.
    pub fn normalized(&self) -> String {
        d3rs::py_str::dash_pattern_separated(self.as_str())
    }
}

/// A separator line: whether it draws, and its color, width and line style.
///
/// Every divider slot is complete in every theme, so a partial write such as
/// `{color: red}` recolors a divider without switching it on or off.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(width, gt = 0)))]
pub struct DividerStyle {
    /// Draw the divider.
    pub visible: bool,
    /// Divider color.
    pub color: Color,
    /// Divider line width in pixels.
    pub width: f64,
    /// Dash pattern in pixels, such as "6 3" (6 drawn, 3 gap); unset draws a solid line.
    #[model(absent = "solid line")]
    pub dasharray: Option<Dasharray>,
}

/// Corner rounding only, for a slot whose renderer never draws a stroke.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct CornerStyle {
    /// Corner radius in pixels.
    pub radius: f64,
}

/// Four-sided spacing (margin or padding) in pixels.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SpacingValues {
    /// Top spacing in pixels.
    #[model(fill = 0.0)]
    pub top: f64,
    /// Right spacing in pixels.
    #[model(fill = 0.0)]
    pub right: f64,
    /// Bottom spacing in pixels.
    #[model(fill = 0.0)]
    pub bottom: f64,
    /// Left spacing in pixels.
    #[model(fill = 0.0)]
    pub left: f64,
}

facet!(
    /// A non-blank glyph: text shown before a table cell's value.
    GlyphText = "glyph_text",
    glyph_text,
    model_core::Keywords::pattern(r"\S")
);

fn glyph_text(v: &str) -> Result<String, String> {
    if v.chars().all(char::is_whitespace) {
        return Err(format!("glyph must be a non-empty string, got {v:?}"));
    }
    Ok(v.to_string())
}

/// A scalar-or-list union the schema IR cannot spell: the facet names it.
macro_rules! union_leaf {
    ($t:ty, $expr:expr) => {
        impl FromNode for $t {
            fn authored(node: &Node, cx: &mut Cx) -> Self::Authored {
                Self::read(node, cx)
            }
            fn patch(node: &Node, cx: &mut Cx) -> Self::Patch {
                Self::read(node, cx)
            }
            fn whole(node: &Node, cx: &mut Cx) -> Option<Self::Whole> {
                Self::read(node, cx)
            }
        }
        impl Leaf for $t {
            type Whole = $t;
            type Authored = Option<$t>;
            type Patch = Option<$t>;
            const MERGE: Merge = Merge::Override;
            const EXPR: TypeExpr = $expr;
        }
        model_core::build_leaf!($t);
    };
}

const fn palette_choice_expr() -> TypeExpr {
    TypeExpr::Either(
        &<Palette as Leaf>::EXPR,
        &TypeExpr::List(&TypeExpr::Scalar(Scalar::Str)),
    )
}

/// A palette: a name, or the colors themselves.
#[derive(Debug, Clone, PartialEq)]
pub enum PaletteChoice {
    Name(Palette),
    Colors(Vec<Color>),
}

impl ReadScalar for PaletteChoice {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        if matches!(node.to_json(), serde_json::Value::Array(_)) {
            return <Vec<Color>>::whole(node, cx).map(Self::Colors);
        }
        Palette::read(node, cx).map(Self::Name)
    }
}
union_leaf!(PaletteChoice, palette_choice_expr());

/// The ink a chart with no color column uses: a palette name or at least one color.
#[derive(Debug, Clone, PartialEq)]
pub struct SeriesInk(pub PaletteChoice);

impl ReadScalar for SeriesInk {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        let choice = PaletteChoice::read(node, cx)?;
        if matches!(&choice, PaletteChoice::Colors(c) if c.is_empty()) {
            cx.invalid(
                node.span,
                "single_series_palette must be a palette name or a non-empty list of colors, got []",
            );
            return None;
        }
        Some(Self(choice))
    }
}
union_leaf!(
    SeriesInk,
    TypeExpr::Keyed(
        &model_core::Keywords::at_least_items(1),
        &palette_choice_expr()
    )
);

/// A table column's palette: a palette name or Vega scheme, or a CSS color list.
#[derive(Debug, Clone, PartialEq)]
pub enum ColumnPalette {
    Name(SchemeName),
    Colors(Vec<Color>),
}

impl ReadScalar for ColumnPalette {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        if matches!(node.to_json(), serde_json::Value::Array(_)) {
            return <Vec<Color>>::whole(node, cx).map(Self::Colors);
        }
        SchemeName::read(node, cx).map(Self::Name)
    }
}
union_leaf!(
    ColumnPalette,
    TypeExpr::Either(
        &<SchemeName as Leaf>::EXPR,
        &TypeExpr::List(&TypeExpr::Scalar(Scalar::Str)),
    )
);

/// A scale's palette: a palette name or Vega scheme, a CSS color list, or relative stops.
#[derive(Debug, Clone, PartialEq)]
pub enum ScalePalette {
    Name(SchemeName),
    Colors(Vec<Color>),
    Stops(Vec<f64>),
}

impl ReadScalar for ScalePalette {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        if matches!(node.to_json(), serde_json::Value::Array(_)) {
            if let Ok(Some(stops)) = cx.attempt(|cx| <Vec<f64>>::whole(node, cx)) {
                return Some(Self::Stops(stops));
            }
            return <Vec<Color>>::whole(node, cx).map(Self::Colors);
        }
        SchemeName::read(node, cx).map(Self::Name)
    }
}
union_leaf!(
    ScalePalette,
    TypeExpr::Either(
        &<SchemeName as Leaf>::EXPR,
        &TypeExpr::Either(
            &TypeExpr::List(&TypeExpr::Scalar(Scalar::Str)),
            &TypeExpr::List(&TypeExpr::Scalar(Scalar::Float)),
        ),
    )
);

string_enum!(
    /// The only domain source a scale names: the data extent.
    DataDomain { Data = "data" }
);
string_enum!(
    /// How diverging scale arms are stretched.
    ArmMode { Asymmetric = "asymmetric", Symmetric = "symmetric" }
);

/// Everything a scale target says beside its palette; no Python counterpart.
///
/// `ColumnScaleTargetConfig` narrows the palette, so the two scale models share these
/// fields through a `#[flatten]` instead of repeating them.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ScaleTargetRest {
    /// Scale domain source. Unset is 'data': the data extent, widened to nice round bounds when nice is true. An explicit null uses the min and max below instead.
    #[model(absent = "an authored null: explicit min/max", fill = "data")]
    pub domain: Option<DataDomain>,
    /// Minimum scale domain value (overrides data minimum).
    #[model(absent = "the data minimum")]
    pub min: Option<crate::unit::Limit>,
    /// Maximum scale domain value (overrides data maximum).
    #[model(absent = "the data maximum")]
    pub max: Option<crate::unit::Limit>,
    /// Widen a data-derived color domain to round ('nice') bounds, e.g. 12-88 to
    /// 0-100, and label every nice tick on a heatmap legend. Unset follows the
    /// legend: it widens where the scale draws a legend (heatmap, map, a chart
    /// color gradient whose legend is shown) and uses the exact extent where none
    /// is drawn (table columns, a hidden legend); true or false always wins.
    /// Ignored once min or max is set; a single-sided bound already fixes that
    /// edge exactly.
    #[model(absent = "follows the legend")]
    pub nice: Option<bool>,
    /// Color assigned to null values.
    #[model(absent = "the scale's default null color")]
    pub null_color: Option<Color>,
    /// Diverging scale midpoint value, or 'auto' to use the data midpoint.
    #[model(absent = "sequential scale: no diverging pivot")]
    pub hinge: Option<Engine<f64, Auto>>,
    /// How diverging scale arms are stretched: 'asymmetric' (proportional) or 'symmetric' (equal arms).
    #[model(fill = "asymmetric")]
    pub arm_mode: ArmMode,
}

/// Scale configuration for a single style target (background or color).
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ScaleTargetConfig {
    /// Which colors the scale draws from: a built-in palette name or Vega scheme, a CSS color list for categorical, or a float list for relative stops.
    pub palette: ScalePalette,
    #[flatten]
    pub rest: ScaleTargetRest,
}

/// Color config for geo, point_map and table families: no categorical arm.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct StaticGradientColorStyle {
    /// One color for all marks: a hex, CSS name, `rgb()`/`oklch()` or theme token (`category[1]`, `positive.solid`, `dbt-grays.8`). A token follows the theme, a hex stays put.
    #[model(absent = "no explicit color")]
    pub r#static: Option<Color>,
    /// Continuous gradient scale for the color encoding.
    #[model(absent = "no gradient")]
    pub gradient: Option<ScaleTargetConfig>,
}

/// Categorical palette config: per-series colors and single-series ink list.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct CategoricalColorStyle {
    /// Palette name, or a list of colors, even one: ['#2E6B4F'].
    #[model(fill = "category")]
    pub palette: PaletteChoice,
    /// Ink without a color column: palette name, or colors list, even one: ['#2E6B4F'].
    pub single_series_palette: SeriesInk,
}

/// Unified chart color config: static paint, categorical palette, gradient scale.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ColorStyle {
    #[flatten]
    pub base: StaticGradientColorStyle,
    /// Palettes used when color encodes distinct categories, or a lone series.
    #[model(absent = "the chart's own palette")]
    pub categorical: Option<CategoricalColorStyle>,
}

/// `style.pattern`: how a chart's `pattern:` channel paints its fills.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(steps, ge = 3, le = 9)))]
pub struct PatternStyle {
    /// Color of the pattern marks, a color or palette token. Default is the theme's single-series color. Not allowed with a `color:` channel, which draws each series' pattern in its own hue.
    #[model(absent = "the theme's single-series color")]
    pub color: Option<Color>,
    /// What the pattern is drawn on: `background` (default), `tint` (a pale tint of the pattern color, drawn with a darker shade of it), a color, or the name of a color palette, whose nth color is the nth series' ground.
    pub ground: Color,
    /// Swap the pattern's ink and ground.
    pub inverse: bool,
    /// Number of ramp steps, 3 to 9, overriding the pattern palette's own count. Only for a quantitative field; a category scale has no steps.
    #[model(absent = "the pattern palette's own step count")]
    pub steps: Option<u32>,
    /// A pattern palette name or a pattern role (`pattern_category`, `pattern_sequential`, `pattern_diverging`). Default follows the field: a discrete field takes `pattern_category`, a quantitative one `pattern_sequential`.
    #[model(absent = "follows the field's kind")]
    pub palette: Option<crate::names::PatternPaletteName>,
}

/// A CSS `box-shadow` minus `inset` and spread, which an SVG drop-shadow filter has neither of:
/// `<x> <y> [<blur>] <color>`.
/// The colors a `box_shadow` or CSS `border` shorthand may name: hex, `rgb()`/`rgba()` or a
/// lowercase CSS color keyword.
macro_rules! css_color {
    () => {
        r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})|rgba?\([^)]*\)|aliceblue|antiquewhite|aqua|aquamarine|azure|beige|bisque|black|blanchedalmond|blue|blueviolet|brown|burlywood|cadetblue|chartreuse|chocolate|coral|cornflowerblue|cornsilk|crimson|cyan|darkblue|darkcyan|darkgoldenrod|darkgr[ae]y|darkgreen|darkkhaki|darkmagenta|darkolivegreen|darkorange|darkorchid|darkred|darksalmon|darkseagreen|darkslateblue|darkslategr[ae]y|darkturquoise|darkviolet|deeppink|deepskyblue|dimgr[ae]y|dodgerblue|firebrick|floralwhite|forestgreen|fuchsia|gainsboro|ghostwhite|gold|goldenrod|gr[ae]y|green|greenyellow|honeydew|hotpink|indianred|indigo|ivory|khaki|lavender|lavenderblush|lawngreen|lemonchiffon|lightblue|lightcoral|lightcyan|lightgoldenrodyellow|lightgr[ae]y|lightgreen|lightpink|lightsalmon|lightseagreen|lightskyblue|lightslategr[ae]y|lightsteelblue|lightyellow|lime|limegreen|linen|magenta|maroon|mediumaquamarine|mediumblue|mediumorchid|mediumpurple|mediumseagreen|mediumslateblue|mediumspringgreen|mediumturquoise|mediumvioletred|midnightblue|mintcream|mistyrose|moccasin|navajowhite|navy|oldlace|olive|olivedrab|orange|orangered|orchid|palegoldenrod|palegreen|paleturquoise|palevioletred|papayawhip|peachpuff|peru|pink|plum|powderblue|purple|rebeccapurple|red|rosybrown|royalblue|saddlebrown|salmon|sandybrown|seagreen|seashell|sienna|silver|skyblue|slateblue|slategr[ae]y|snow|springgreen|steelblue|tan|teal|thistle|tomato|transparent|turquoise|violet|wheat|white|whitesmoke|yellow|yellowgreen"
    };
}

const BOX_SHADOW_PATTERN: &str = concat!(
    r"^(-?[0-9]+(?:\.[0-9]+)?)(?:px)? (-?[0-9]+(?:\.[0-9]+)?)(?:px)?(?: ([0-9]+(?:\.[0-9]+)?)(?:px)?)? (",
    css_color!(),
    r")$"
);

/// A CSS `border` shorthand, `[<n>px] [solid|dashed|dotted] [<color>]`, as Python states it.
const BORDER_STRING_PATTERN: &str = concat!(
    r"^(?=\S)(?!.*\s$)(?:([0-9]+(?:\.[0-9]+)?)px(?: |$))?(?:(solid|dashed|dotted)(?: |$))?(",
    css_color!(),
    r")?$"
);

const BORDER_DASHES: [(&str, [f64; 2], Option<&str>); 2] = [
    ("dashed", [4.0, 4.0], None),
    ("dotted", [1.0, 3.0], Some("round")),
];

const BORDER_REFUSED: &str = "Value error, border must look like '2px dashed #333' (width in px, solid/dashed/dotted, hex/rgb()/CSS color keyword), got ";

/// `border: 2px dashed #333`: Python's `BorderStylePatch.from_css`, which widens the string into
/// the mapping it stands for. Radius is never read from the string.
fn border_css(node: &model_core::Node, cx: &mut model_core::Cx) -> model_core::Shorthand {
    use model_core::{map_node, scalar_node, Kind, Shorthand};
    use yaml::Value;
    static PARTS: OnceLock<Option<[Regex; 3]>> = OnceLock::new();
    let Kind::Scalar(Value::Str(text)) = &node.kind else {
        return Shorthand::Keep;
    };
    let parts = PARTS
        .get_or_init(|| {
            Some([
                Regex::new(r"^([0-9]+(?:\.[0-9]+)?)px(?: |$)").ok()?,
                Regex::new(r"^(solid|dashed|dotted)(?: |$)").ok()?,
                Regex::new(concat!("^(?:", css_color!(), ")$")).ok()?,
            ])
        })
        .as_ref();
    let span = node.span;
    let refuse = |cx: &mut model_core::Cx| {
        let got = diagnostic::py_repr(&serde_json::Value::String(text.clone()));
        cx.invalid(span, format!("{BORDER_REFUSED}{got}"));
        Shorthand::Refused
    };
    let ws = char::is_whitespace;
    let Some([width, style, color]) = parts else {
        return refuse(cx);
    };
    if text.is_empty() || text.starts_with(ws) || text.ends_with(ws) {
        return refuse(cx);
    }
    let mut rest = text.as_str();
    let take = |re: &Regex, rest: &mut &str| {
        re.captures(rest).map(|c| {
            let whole = c.get(0).map_or(0, |m| m.end());
            let group = c.get(1).map_or("", |m| m.as_str()).to_string();
            *rest = rest.get(whole..).unwrap_or("");
            group
        })
    };
    let width_px = take(width, &mut rest);
    let line = take(style, &mut rest);
    let color_text = match rest {
        "" => None,
        c if color.is_match(c) => Some(c),
        _ => return refuse(cx),
    };
    let num = |v: f64| serde_json::Number::from_f64(v).map_or(Value::Null, Value::Number);
    let mut entries = Vec::new();
    if let Some((_, dash, cap)) = BORDER_DASHES.iter().find(|d| Some(d.0) == line.as_deref()) {
        let items = dash.iter().map(|d| scalar_node(span, num(*d))).collect();
        entries.push((
            "dash_array",
            model_core::Node {
                span,
                kind: Kind::Seq(items),
            },
        ));
        if let Some(cap) = cap {
            entries.push((
                "line_cap",
                scalar_node(span, Value::Str((*cap).to_string())),
            ));
        }
    }
    if let Some(w) = width_px.and_then(|w| w.parse::<f64>().ok()) {
        entries.push(("width", scalar_node(span, num(w))));
    }
    if let Some(c) = color_text {
        entries.push(("color", scalar_node(span, Value::Str(c.to_string()))));
    }
    Shorthand::Widened(map_node(span, entries))
}

fn box_shadow(v: &str) -> Result<String, String> {
    static PATTERN: OnceLock<Option<Regex>> = OnceLock::new();
    let pattern = PATTERN
        .get_or_init(|| Regex::new(BOX_SHADOW_PATTERN).ok())
        .as_ref();
    if pattern.is_some_and(|p| p.is_match(v)) {
        return Ok(v.to_string());
    }
    Err("box_shadow must look like '0 2px 8px #00000040' (x offset, y offset, optional blur, hex/rgb()/CSS color keyword; no inset or spread)".to_string())
}

facet!(
    /// A drop shadow under chart cards: x offset, y offset, optional blur, color.
    BoxShadow = "box_shadow",
    box_shadow,
    model_core::Keywords::pattern(BOX_SHADOW_PATTERN)
);

string_enum!(
    /// Notation style of a formatted number.
    Notation { Analytic = "analytic", Narrative = "narrative" }
);

string_enum!(
    /// Where a negative sign sits against the prefix.
    SignPlacement { BeforePrefix = "before_prefix", AfterPrefix = "after_prefix" }
);

string_enum!(
    /// Whether a prefix or suffix paints on every value or on one anchor value.
    AffixRepeat { Every = "every", Anchor = "anchor" }
);

/// Format configuration for value display: a format string alone, or a mapping with `spec`.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(shorthand = "spec")]
pub struct FormatConfig {
    /// D3 format string (`,.0f`; `.2s` writes 1.2M), preset name (e.g., 'currency'), or Excel pattern. A format is this string alone, or a mapping with `spec`. A date also takes strftime (`%b '%y`).
    #[model(absent = "no spec")]
    pub spec: Option<String>,
    /// Text placed before the formatted value (e.g., '$').
    #[model(absent = "no prefix")]
    pub prefix: Option<String>,
    /// Text placed after the formatted value (e.g., ' USD', '%').
    #[model(absent = "no suffix")]
    pub suffix: Option<String>,
    /// Notation style: 'analytic' for SI-prefix (1B, 1M) or 'narrative' for prose-style (1bn, 1m).
    #[model(absent = "the format's own notation")]
    pub notation: Option<Notation>,
    /// Where a negative sign sits against the prefix: 'before_prefix' (−€500) or 'after_prefix' (EUR −500). Unset: after_prefix when the prefix ends in a space or paints on one value only, else before_prefix. Table columns keep the sign with the digits and reject it.
    #[model(absent = "decided by the prefix")]
    pub sign_placement: Option<SignPlacement>,
    /// Whether the prefix/suffix paints on 'every' value of an axis, table column or support_table row, or on one 'anchor' value. Unset keeps each surface's default. Single-value slots ignore it.
    #[model(absent = "each surface's default")]
    pub repeat: Option<AffixRepeat>,
}

string_enum!(
    /// How a click opens a link's target.
    LinkOpen { Page = "page", Modal = "modal", Preview = "preview", NewTab = "new_tab" }
);

/// A `format:` value: a name or spec alone, or a block. A layer replaces it whole, so it is a
/// union leaf, not a model that merges key by key.
#[derive(Debug, Clone, PartialEq)]
pub enum FormatSpec {
    Name(FormatName),
    Block(<FormatConfig as Leaf>::Whole),
}

impl ReadScalar for FormatSpec {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        if matches!(node.kind, model_core::Kind::Map(_)) {
            return FormatConfig::whole(node, cx).map(Self::Block);
        }
        FormatName::read(node, cx).map(Self::Name)
    }
}

impl FromNode for FormatSpec {
    fn authored(node: &Node, cx: &mut Cx) -> Self::Authored {
        Self::read(node, cx)
    }
    fn patch(node: &Node, cx: &mut Cx) -> Self::Patch {
        Self::read(node, cx)
    }
    fn whole(node: &Node, cx: &mut Cx) -> Option<Self::Whole> {
        Self::read(node, cx)
    }
}

impl Leaf for FormatSpec {
    type Whole = FormatSpec;
    type Authored = Option<FormatSpec>;
    type Patch = Option<FormatSpec>;
    const MERGE: Merge = Merge::Override;
    const EXPR: TypeExpr = TypeExpr::Either(
        &TypeExpr::Ref("FormatConfig", <FormatConfig as model_core::Model>::info),
        &<FormatName as Leaf>::EXPR,
    );
}
model_core::build_leaf!(FormatSpec);

/// A `link:` value: a URL alone, or an object that also chooses how a click opens. A layer
/// replaces it whole.
#[derive(Debug, Clone, PartialEq)]
pub enum LinkSpec {
    Url(Url),
    Block(<Link as Leaf>::Whole),
}

impl ReadScalar for LinkSpec {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        if matches!(node.kind, model_core::Kind::Map(_)) {
            return Link::whole(node, cx).map(Self::Block);
        }
        Url::read(node, cx).map(Self::Url)
    }
}

impl FromNode for LinkSpec {
    fn authored(node: &Node, cx: &mut Cx) -> Self::Authored {
        Self::read(node, cx)
    }
    fn patch(node: &Node, cx: &mut Cx) -> Self::Patch {
        Self::read(node, cx)
    }
    fn whole(node: &Node, cx: &mut Cx) -> Option<Self::Whole> {
        Self::read(node, cx)
    }
}

impl Leaf for LinkSpec {
    type Whole = LinkSpec;
    type Authored = Option<LinkSpec>;
    type Patch = Option<LinkSpec>;
    const MERGE: Merge = Merge::Override;
    const EXPR: TypeExpr = TypeExpr::Either(
        &TypeExpr::Ref("Link", <Link as model_core::Model>::info),
        &<Url as Leaf>::EXPR,
    );
}
model_core::build_leaf!(LinkSpec);

/// Object form of a `link:`; a bare string takes the board's default open.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(shorthand = "url")]
pub struct Link {
    /// Click-through URL template.
    pub url: String,
    /// How a click opens the target: page navigates the board away; modal opens the target board in a dialog over this one; preview shows it small, beside the clicked mark; new_tab opens it in a new browser tab. Unset takes the board's links.open.
    #[model(absent = "takes the board's links.open")]
    pub open: Option<LinkOpen>,
}
