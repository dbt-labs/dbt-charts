//! The `models/primitives.py` models the theme files embed, other than the font
//! slot (`style::font`): strokes, borders and their closed vocabularies.

use crate::facet::{facet, BorderColor, Color, Facet, FormatName, Palette, SchemeName, Url};
use crate::rule::Auto;
use crate::string_enum;
use crate::unit::Px;
use diagnostic::{diag, Diagnostic};
use model_core::{Bounds, Cx, Engine, FromNode, Keywords, Leaf, Merge, Node, ReadScalar, Scalar, Spell, TypeExpr};
use model_derive::Model;
use regex::Regex;
use serde_json::Value;
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
    /// How the stroke's ends are finished (butt, round, or square). Absent leaves
    /// `stroke-linecap` out of the SVG, so its `butt` default applies.
    #[model(absent = "no stroke-linecap attribute: the SVG default (butt) applies")]
    pub cap: Option<Engine<LineCap, Auto>>,
    /// How two stroke segments are joined (miter, round, or bevel). Absent leaves
    /// `stroke-linejoin` out of the SVG, so its `miter` default applies.
    #[model(absent = "no stroke-linejoin attribute: the SVG default (miter) applies")]
    pub join: Option<Engine<LineJoin, Auto>>,
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

/// Four-sided spacing (margin or padding) in pixels. A bare number is every side, and a string is
/// one to four CSS lengths (`16px`, `8px 16px`, `8px 16px 12px`, `8px 16px 12px 4px`).
#[derive(Debug, Clone, PartialEq, Model)]
#[model(shorthand_with = spacing_css, string_pattern = SPACING_STRING_PATTERN)]
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

const SPACING_STRING_PATTERN: &str =
    r"^[0-9]+(?:\.[0-9]+)?(?:px)?(?: [0-9]+(?:\.[0-9]+)?(?:px)?){0,3}$";

/// `padding: 30` and `padding: 8px 16px`: Python's `_parse_spacing` and `SpacingValues.from_css`,
/// which widen a number or a string into the four sides it stands for.
fn spacing_css(node: &model_core::Node, cx: &mut model_core::Cx) -> model_core::Shorthand {
    use model_core::{map_node, scalar_node, Kind, Shorthand};
    use yaml::Value;
    let span = node.span;
    let sides = |top: Value, right: Value, bottom: Value, left: Value| {
        Shorthand::Widened(map_node(
            span,
            vec![
                ("top", scalar_node(span, top)),
                ("right", scalar_node(span, right)),
                ("bottom", scalar_node(span, bottom)),
                ("left", scalar_node(span, left)),
            ],
        ))
    };
    match &node.kind {
        Kind::Scalar(Value::Bool(b)) => {
            let got = if *b { "True" } else { "False" };
            cx.invalid(
                span,
                format!("Value error, spacing must be a number or a CSS string, got {got}"),
            );
            Shorthand::Refused
        }
        Kind::Scalar(n @ Value::Number(_)) => sides(n.clone(), n.clone(), n.clone(), n.clone()),
        Kind::Scalar(Value::Str(text)) => {
            let lengths: Option<Vec<f64>> = text
                .split(' ')
                .map(crate::unit::pixel_length)
                .collect();
            let Some(v) = lengths.filter(|v| (1..=4).contains(&v.len())) else {
                let got = diagnostic::py_repr(&serde_json::Value::String(text.clone()));
                cx.invalid(
                    span,
                    format!("Value error, spacing must be 1-4 pixel lengths such as '16px' or '8px 16px', got {got}"),
                );
                return Shorthand::Refused;
            };
            let num = |v: f64| serde_json::Number::from_f64(v).map_or(Value::Null, Value::Number);
            let at = |i: usize| v.get(i).copied().unwrap_or_default();
            let (top, right, bottom, left) = match v.len() {
                1 => (at(0), at(0), at(0), at(0)),
                2 => (at(0), at(1), at(0), at(1)),
                3 => (at(0), at(1), at(2), at(1)),
                _ => (at(0), at(1), at(2), at(3)),
            };
            sides(num(top), num(right), num(bottom), num(left))
        }
        _ => Shorthand::Keep,
    }
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
impl Spell for PaletteChoice {
    fn spell(&self) -> Value {
        match self {
            Self::Name(n) => n.spell(),
            Self::Colors(c) => c.spell(),
        }
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
impl Spell for SeriesInk {
    fn spell(&self) -> Value {
        self.0.spell()
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
impl Spell for ColumnPalette {
    fn spell(&self) -> Value {
        match self {
            Self::Name(n) => n.spell(),
            Self::Colors(c) => c.spell(),
        }
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
impl Spell for ScalePalette {
    fn spell(&self) -> Value {
        match self {
            Self::Name(n) => n.spell(),
            Self::Colors(c) => c.spell(),
            Self::Stops(s) => s.spell(),
        }
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
    /// How diverging scale arms are stretched.
    ArmMode { Asymmetric = "asymmetric", Symmetric = "symmetric" }
);

const DOMAIN_END: TypeExpr = TypeExpr::Option(&TypeExpr::Either(
    &TypeExpr::Scalar(Scalar::Int),
    &TypeExpr::Scalar(Scalar::Float),
));
const DOMAIN_ITEMS: TypeExpr = TypeExpr::Tuple(&[&DOMAIN_END, &DOMAIN_END]);

/// The values a color scale's ends map to; `[min, max]` is its shorthand.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(
    shorthand_with = domain_list,
    shorthand_items = DOMAIN_ITEMS,
    constraint(at_least_one_of(min, max,
        message = "domain pins neither end: omit `domain` to fit both ends to the data."
    ))
)]
pub struct ScaleDomain {
    /// Value the scale's low end maps to; unset fits the data.
    #[model(absent = "the data minimum")]
    pub min: Option<crate::unit::Number>,
    /// Value the scale's high end maps to; unset fits the data.
    #[model(absent = "the data maximum")]
    pub max: Option<crate::unit::Number>,
}

/// `domain: [min, max]`: Python's `domain_shorthand`, which widens a two-item list into the
/// `{min, max}` mapping it stands for and leaves a null end unset.
pub fn domain_list(node: &model_core::Node, cx: &mut model_core::Cx) -> model_core::Shorthand {
    use model_core::{map_node, Kind, Shorthand};
    let Kind::Seq(items) = &node.kind else {
        return Shorthand::Keep;
    };
    let [min, max] = items.as_slice() else {
        cx.invalid(
            node.span,
            format!(
                "Value error, domain shorthand is [min, max], got {} values. Write null for an end that fits the data.",
                items.len()
            ),
        );
        return Shorthand::Refused;
    };
    let ends: Vec<_> = [("min", min), ("max", max)]
        .into_iter()
        .filter(|(_, end)| !end.to_json().is_null())
        .map(|(key, end)| (key, end.clone()))
        .collect();
    if ends.is_empty() {
        cx.invalid(
            node.span,
            "Value error, domain pins neither end: omit `domain` to fit both ends to the data."
                .to_string(),
        );
        return Shorthand::Refused;
    }
    Shorthand::Widened(map_node(node.span, ends))
}

/// Everything a scale target says beside its palette; no Python counterpart.
///
/// `ColumnScaleTargetConfig` narrows the palette, so the two scale models share these
/// fields through a `#[flatten]` instead of repeating them.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ScaleTargetRest {
    /// Values the scale's ends map to, as [min, max]; unset fits the data.
    #[model(absent = "fits the data")]
    pub domain: Option<ScaleDomain>,
    /// Widen a data-derived color domain to round ('nice') bounds, e.g. 12-88 to
    /// 0-100, and label every nice tick on a heatmap legend. Unset follows the
    /// legend: it widens where the scale draws a legend (heatmap, map, a chart
    /// color gradient whose legend is shown) and uses the exact extent where none
    /// is drawn (table columns, a hidden legend); true or false always wins.
    /// Ignored once either end of domain is set; a one-sided bound already fixes
    /// that edge exactly.
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

/// Color-scale config for the geo families, no categorical arm. Constant paint lives on the chart's primary mark.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct GradientColorStyle {
    /// Continuous gradient scale for the color encoding.
    #[model(absent = "no gradient", local)]
    pub gradient: Option<ScaleTargetConfig>,
}

/// Categorical palette config: per-series colors and single-series ink list.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct CategoricalColorStyle {
    /// Palette name, or a list of colors, even one: ['#2E6B4F'].
    #[model(fill = "category", local)]
    pub palette: PaletteChoice,
    /// Ink without a color column: palette name, or colors list, even one: ['#2E6B4F'].
    pub single_series_palette: SeriesInk,
}

/// Unified chart color config: categorical palette and gradient scale.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ColorStyle {
    #[flatten]
    pub base: GradientColorStyle,
    /// Palettes used when color encodes distinct categories, or a lone series.
    #[model(absent = "the chart's own palette")]
    pub categorical: Option<CategoricalColorStyle>,
}

string_enum!(
    /// How a size-channel value maps onto the mark-area range.
    SizeScaleType { Linear = "linear", Sqrt = "sqrt", Log = "log" }
);

/// A mark-area range `[low, high]` in px², `0 <= low < high`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct AreaRange {
    pub low: f64,
    pub high: f64,
}

const AREA: TypeExpr = TypeExpr::Keyed(
    &Keywords::bounded(Bounds { ge: Some(0.0), ..Bounds::NONE }),
    &TypeExpr::Scalar(Scalar::Float),
);

impl ReadScalar for AreaRange {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        let Value::Array(items) = node.to_json() else {
            cx.mismatch(node, "a list of two numbers");
            return None;
        };
        let [low_node, high_node] = items.as_slice() else {
            let side = if items.len() < 2 { "at least" } else { "at most" };
            cx.invalid(
                node.span,
                format!("Tuple should have {side} 2 items after validation, not {}", items.len()),
            );
            return None;
        };
        let (Some(low), Some(high)) = (low_node.as_f64(), high_node.as_f64()) else {
            cx.mismatch(node, "two numbers");
            return None;
        };
        if low < 0.0 || high < 0.0 {
            cx.invalid(node.span, "Input should be greater than or equal to 0".to_string());
            return None;
        }
        if low >= high {
            cx.invalid(
                node.span,
                format!("Value error, style.size.range: low ({low}) must be below high ({high})."),
            );
            return None;
        }
        Some(Self { low, high })
    }
}
impl Spell for AreaRange {
    fn spell(&self) -> Value {
        Value::Array(vec![self.low.spell(), self.high.spell()])
    }
}
impl FromNode for AreaRange {
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
impl Leaf for AreaRange {
    type Whole = AreaRange;
    type Authored = Option<AreaRange>;
    type Patch = Option<AreaRange>;
    const MERGE: Merge = Merge::Override;
    const EXPR: TypeExpr = TypeExpr::Tuple(&[&AREA, &AREA]);
}
model_core::build_leaf!(AreaRange);

/// Size-channel scale config: the mark-area range and the scale law.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SizeScaleStyle {
    /// Mark area in px² for the smallest and largest value, as [low, high] with 0 <= low < high (same unit as `style.marks.point.size`).
    pub range: AreaRange,
    /// How a value maps onto the area range. `linear` is zero-anchored; `sqrt` compresses a long tail while keeping zero; `log` spans the data extent and needs every size value to be above zero.
    pub r#type: SizeScaleType,
}

impl SizeScaleStyle {
    /// Python's `SizeScaleStyle` rule on the merged style: log maps the smallest value to
    /// `range.low`, so a range starting at 0 draws that mark with no area. Hand-written because
    /// it reads two fields, which no `constraint` kind does.
    pub fn log_range_starts_above_zero(&self) -> Vec<Diagnostic> {
        if self.r#type != SizeScaleType::Log || self.range.low != 0.0 {
            return Vec::new();
        }
        vec![diag::err_validation_field(
            "style.size",
            "Value error, style.size.type: log needs a range starting above 0; the smallest value would draw with no area. Set style.size.range, e.g. [9, 2500].".to_string(),
        )]
    }
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

impl Spell for FormatSpec {
    fn spell(&self) -> Value {
        match self {
            Self::Name(n) => n.spell(),
            Self::Block(b) => b.spell(),
        }
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

impl Spell for LinkSpec {
    fn spell(&self) -> Value {
        match self {
            Self::Url(u) => u.spell(),
            Self::Block(b) => b.spell(),
        }
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

/// Board-wide link behavior: automatic links and how links open.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct BoardLinks {
    /// When True, table charts with no explicit link: automatically link each row to its canonical /data/<source>/<schema>/<table>/detail/ page. Default off. An explicit link: always wins; set link: false on a chart to suppress its automatic link.
    #[model(fill = false)]
    pub auto: bool,
    /// How a link that does not author its own open opens: page, modal, preview or new_tab. Query-only ?variable= links, a table cell's filter link, implied email/url and KPI links, and a custom chart's per-row link always open as a page.
    #[model(fill = "page")]
    pub open: LinkOpen,
}
