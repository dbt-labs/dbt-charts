//! `style/theme/annotations.py`: the theme defaults for rules, bands, notes and trends.
//!
//! Python models "not set at this tier" as `None`; the spine is the total
//! normalized shape, so those leaves are plain `T` (the authored twin is
//! `Option<T>`) and only `Absent` fields are `Option<T>`. A leaf marked
//! `SkipInheritSlots()` is `local`: the enclosing `marks` slot does not mirror it.
//! `SkipInheritSlots(cascade=True)` is not `local` (it still mirrors leaves).
//!
//! A Python subclass that overrides a field cannot be `#[flatten]` composition
//! (the key would repeat), so those subclasses are models of their own and share
//! the unchanged fields through [`FontSlot`] and [`TextMarkRest`], which have no
//! Python counterpart.

use crate::facet::{Color, Facet, FontFamily};
use crate::rule::Auto;
use crate::string_enum;
use crate::style::font::FontSlant;
use crate::style::legend::{LegendAlign, LegendEdge};
use crate::unit::{Fraction, Px};
use diagnostic::py_repr;
use model_core::Engine;
use model_derive::Model;
use serde_json::Value;

string_enum!(
    /// How an annotation's line is interpolated.
    AnnotationCurve { Linear = "linear", Natural = "natural" }
);
string_enum!(
    /// A rule label's color keyword: the color of the rule's own line.
    MatchLine { Line = "line" }
);

string_enum!(
    /// The weights an annotation font names.
    WeightName { Normal = "normal", Bold = "bold", Bolder = "bolder", Lighter = "lighter" }
);

/// How heavy an annotation's type is drawn: a number above zero, or a name.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum AnnotationFontWeight {
    Name(WeightName),
    Number(f64),
}

impl Facet for AnnotationFontWeight {
    const FACET: &'static str = "font_weight";
}

impl TryFrom<f64> for AnnotationFontWeight {
    type Error = String;
    fn try_from(v: f64) -> Result<Self, String> {
        if v > 0.0 {
            Ok(Self::Number(v))
        } else {
            Err("Input should be greater than 0".to_string())
        }
    }
}

impl TryFrom<&str> for AnnotationFontWeight {
    type Error = String;
    fn try_from(v: &str) -> Result<Self, String> {
        WeightName::try_from(v).map(Self::Name)
    }
}

impl model_core::ReadScalar for AnnotationFontWeight {
    fn read(node: &model_core::Node, cx: &mut model_core::Cx) -> Option<Self> {
        let read = if let Ok(Some(n)) = cx.attempt(|cx| f64::read(node, cx)) {
            Self::try_from(n)
        } else {
            Self::try_from(String::read(node, cx)?.as_str())
        };
        read.map_err(|e| cx.invalid(node.span, e)).ok()
    }
}

impl model_core::FromNode for AnnotationFontWeight {
    fn authored(node: &model_core::Node, cx: &mut model_core::Cx) -> Self::Authored {
        <Self as model_core::ReadScalar>::read(node, cx)
    }
    fn patch(node: &model_core::Node, cx: &mut model_core::Cx) -> Self::Patch {
        <Self as model_core::ReadScalar>::read(node, cx)
    }
    fn whole(node: &model_core::Node, cx: &mut model_core::Cx) -> Option<Self::Whole> {
        <Self as model_core::ReadScalar>::read(node, cx)
    }
}

impl model_core::Leaf for AnnotationFontWeight {
    type Whole = AnnotationFontWeight;
    type Authored = Option<AnnotationFontWeight>;
    type Patch = Option<AnnotationFontWeight>;
    const MERGE: model_core::Merge = model_core::Merge::Override;
    const EXPR: model_core::TypeExpr = model_core::TypeExpr::Keyed(
        &model_core::Keywords {
            pattern: Some("^(?!auto$)"),
            in_engine: model_core::InEngine::Drop,
            bounds: model_core::Bounds {
                gt: Some(0.0),
                ..model_core::Bounds::NONE
            },
            min_items: None,
            max_items: None,
        },
        &model_core::TypeExpr::Either(
            &model_core::TypeExpr::Ref("WeightName", <WeightName as model_core::Model>::info),
            &model_core::TypeExpr::Scalar(model_core::Scalar::Float),
        ),
    );
}
model_core::build_leaf!(AnnotationFontWeight);

/// A dash pattern: `auto`, or positive numbers separated by spaces or commas.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Dasharray(String);

impl Dasharray {
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

fn is_number(t: &str) -> bool {
    let digits = |s: &str| !s.is_empty() && s.chars().all(|c| c.is_ascii_digit());
    match t.split_once('.') {
        Some((whole, frac)) => digits(whole) && digits(frac),
        None => digits(t),
    }
}

impl TryFrom<&str> for Dasharray {
    type Error = String;
    fn try_from(v: &str) -> Result<Self, String> {
        let mut parts = v.trim().split([' ', ',']).peekable();
        let first_ok = parts.peek().is_some_and(|p| !p.is_empty());
        let mut last_ok = first_ok;
        let all_ok = parts.all(|p| {
            last_ok = !p.is_empty();
            p.is_empty() || is_number(p)
        });
        if v == "auto" || (first_ok && last_ok && all_ok) {
            return Ok(Self(v.to_string()));
        }
        Err(format!(
            "dasharray must be `auto` or positive numbers such as '6 3', got {}",
            py_repr(&Value::String(v.to_string()))
        ))
    }
}

impl TryFrom<String> for Dasharray {
    type Error = String;
    fn try_from(v: String) -> Result<Self, String> {
        Self::try_from(v.as_str())
    }
}

impl model_core::Leaf for Dasharray {
    type Whole = Dasharray;
    type Authored = Option<Dasharray>;
    type Patch = Option<Dasharray>;
    const MERGE: model_core::Merge = model_core::Merge::Override;
    const EXPR: model_core::TypeExpr = model_core::TypeExpr::Keyed(
        &model_core::Keywords::pattern(crate::primitives::DASHARRAY_PATTERN_WITH_AUTO),
        &model_core::TypeExpr::Scalar(model_core::Scalar::Str),
    );
}
model_core::build_leaf!(Dasharray);

model_core::from_scalar!(Dasharray, String);

/// An annotation mark's outline: color, width and dash pattern.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AnnotationStrokeStyle {
    /// Stroke color.
    pub color: Color,
    /// Stroke width in pixels.
    pub width: Px,
    /// Dash pattern: lengths of dashes and blanks (e.g. '4 2'), or `auto` for a
    /// pattern sized to the width.
    #[model(absent = "solid line")]
    pub dasharray: Option<Dasharray>,
}

/// A rule's line, whose color may be left to the engine.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct RuleStrokeStyle {
    /// Stroke color. `auto` draws the quietest gray of the theme's chrome ladder
    /// that holds 3:1 against the canvas and the marks the rule crosses.
    pub color: Engine<Color, Auto>,
    /// Stroke width in pixels.
    pub width: Px,
    /// Dash pattern: lengths of dashes and blanks (e.g. '4 2'), or `auto` for a
    /// pattern sized to the width.
    #[model(absent = "solid line")]
    pub dasharray: Option<Dasharray>,
}

/// The outline of the box behind an annotation's text.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AnnotationBoxStrokeStyle {
    /// Outline color.
    pub color: Color,
    /// Outline width in pixels.
    pub width: Px,
}

/// The font fields every annotation font slot shares; `color` differs per slot.
#[derive(Debug, Clone, PartialEq, Model)]
// One `range` names one field, so two bounds are two constraints.
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(range(size, gt = 0)),
    constraint(range(line_height, gt = 0))
)]
pub struct FontSlot {
    /// Name of a `style.fonts` entry this slot starts from; the fields set beside
    /// it override that entry.
    #[model(absent = "no named font")]
    pub extends: Option<String>,
    /// Font family or CSS stack; single-quote multiword names: "'Source Serif 4', serif".
    pub family: FontFamily,
    /// Font size in pixels.
    pub size: f64,
    /// How heavy the type is drawn: a number such as 400 or 700, or normal, bold,
    /// bolder, lighter.
    pub weight: AnnotationFontWeight,
    /// Upright or slanted type (normal or italic).
    pub style: FontSlant,
    /// Line height as a unitless multiple of font size.
    pub line_height: f64,
}

/// The font of an annotation's text: the fields the engine sets type with.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(shorthand = "extends")]
pub struct AnnotationFontStyle {
    #[flatten]
    pub slot: FontSlot,
    /// Text color as a CSS color string.
    pub color: Color,
}

/// A rule's label font, whose color may follow the rule's line.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(shorthand = "extends")]
pub struct RuleFontStyle {
    #[flatten]
    pub slot: FontSlot,
    /// Text color as a CSS color string; `line` is the rule's line color.
    pub color: Engine<Color, MatchLine>,
}

/// Which side of the annotation the text sits on, and where along it.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AnnotationPositionStyle {
    /// Side of the annotation the text sits on.
    pub edge: LegendEdge,
    /// Position along the edge.
    pub align: LegendAlign,
    /// True draws the text over the plot; False reserves room for it.
    pub overlay: bool,
}

/// A band's text position; `auto` follows the band's direction.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct BandPositionStyle {
    /// Side of the band the text sits on; `auto` is top for an x band, right for a y band.
    pub edge: Engine<LegendEdge, Auto>,
    /// Position along the edge; `auto` is center.
    pub align: Engine<LegendAlign, Auto>,
    /// True draws the text over the plot; False reserves room for it; `auto` is True.
    pub overlay: Engine<bool, Auto>,
}

/// A rule's text position; `auto` lets placement move it off the chart's ink.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct RulePositionStyle {
    /// Side of the rule the text sits on; `auto` is top, or another side when that one is taken.
    pub edge: Engine<LegendEdge, Auto>,
    /// Position along the edge; `auto` is end, or another spot when that one is taken.
    pub align: Engine<LegendAlign, Auto>,
    /// True draws the text over the plot; False reserves room for it.
    pub overlay: bool,
}

/// A note's text position; `auto` follows the value axis.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct NotePositionStyle {
    /// Side of the anchor the text sits on; `auto` is past the value: top on a
    /// vertical chart, right on a horizontal one.
    pub edge: Engine<LegendEdge, Auto>,
    /// Position along the edge; `auto` is center, or another spot when that one is taken.
    pub align: Engine<LegendAlign, Auto>,
    /// True draws the text over the plot; False reserves room for it.
    pub overlay: bool,
}

/// Box drawn behind an annotation's text.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AnnotationTextBoxStyle {
    /// Draw a box behind the text.
    pub visible: bool,
    /// Box fill color.
    pub fill: Color,
    /// Box fill opacity, 0 to 1.
    pub opacity: Fraction,
    /// Space between the text and the box edge, in pixels.
    pub padding: Px,
    /// Corner radius of the box, in pixels.
    pub radius: Px,
    /// Outline of the box.
    #[model(absent = "no outline")]
    pub stroke: Option<AnnotationBoxStrokeStyle>,
}

/// The text-mark fields every annotation text slot shares. The global text mark
/// has none of them to fall back to, so each is `local`.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TextMarkRest {
    /// Width of the canvas-colored outline behind the text, in pixels; 0 draws
    /// none. `auto` draws a 3px outline only on text that overlaps a bar, point
    /// or area fill.
    #[model(local)]
    pub halo: Engine<Px, Auto>,
    /// Opacity of the halo as a whole, 0 to 1; overlapping letter outlines do not darken.
    #[model(local)]
    pub halo_opacity: Fraction,
    /// Box drawn behind the text.
    #[model(local)]
    pub r#box: AnnotationTextBoxStyle,
    /// Horizontal nudge after placement, in pixels.
    #[model(local)]
    pub dx: f64,
    /// Vertical nudge after placement, in pixels.
    #[model(local)]
    pub dy: f64,
}

/// Annotation text: its font, halo, box, placement and nudge.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AnnotationTextMarkStyle {
    #[flatten]
    pub rest: TextMarkRest,
    /// Text font style.
    #[model(inherit_slot = "Style.charts.font")]
    pub font: AnnotationFontStyle,
    /// Which side of the annotation the text sits on, and where along it.
    #[model(local)]
    pub position: AnnotationPositionStyle,
}

/// A band's text, whose position may follow the band's direction.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct BandTextMarkStyle {
    #[flatten]
    pub rest: TextMarkRest,
    /// Text font style.
    #[model(inherit_slot = "Style.charts.font")]
    pub font: AnnotationFontStyle,
    /// Which side of the band the text sits on, and where along it.
    #[model(local)]
    pub position: BandPositionStyle,
}

/// A rule's text.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct RuleTextMarkStyle {
    #[flatten]
    pub rest: TextMarkRest,
    /// Text font style.
    #[model(inherit_slot = "Style.charts.font")]
    pub font: RuleFontStyle,
    /// Which side of the rule the text sits on, and where along it.
    #[model(local)]
    pub position: RulePositionStyle,
}

/// A note's text, plus the measure a note with no anchor wraps to.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(max_width, gt = 0)))]
pub struct NoteTextMarkStyle {
    #[flatten]
    pub rest: TextMarkRest,
    /// Text font style.
    #[model(inherit_slot = "Style.charts.font")]
    pub font: AnnotationFontStyle,
    /// Which side of the anchor the text sits on, and where along it.
    #[model(local)]
    pub position: NotePositionStyle,
    /// Width in pixels a note with no anchor wraps its text to; `auto` is a
    /// readable measure of about 28 characters, kept inside the plot. A note
    /// anchored at a point does not wrap.
    #[model(local)]
    pub max_width: Engine<f64, Auto>,
}

/// A trend's text, plus the measure it wraps to.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(max_width, gt = 0)))]
pub struct TrendTextMarkStyle {
    #[flatten]
    pub text: AnnotationTextMarkStyle,
    /// Width in pixels a trend's text wraps to; `auto` is a readable measure of
    /// about 28 characters, kept inside the plot.
    #[model(local)]
    pub max_width: Engine<f64, Auto>,
}

/// A note's connector or a trend's curve: stroke, curve and opacity.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AnnotationLineMarkStyle {
    /// Line stroke style.
    pub stroke: AnnotationStrokeStyle,
    /// Line interpolation: `linear` is straight, `natural` bends a note's
    /// connector and smooths a trend's curve.
    #[model(local)]
    pub curve: AnnotationCurve,
    /// Line opacity, 0 to 1.
    #[model(local)]
    pub opacity: Fraction,
}

/// A note's connector: the annotation line plus its length.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct NoteLineMarkStyle {
    #[flatten]
    pub line: AnnotationLineMarkStyle,
    /// Distance between the anchor and the note's text when a connector is drawn, in pixels.
    #[model(local)]
    pub length: Px,
}

/// A trend's interval: opacity and fill.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AnnotationAreaMarkStyle {
    /// Interval fill opacity, 0 to 1.
    #[model(local)]
    pub opacity: Fraction,
    /// Interval fill color.
    #[model(local)]
    pub fill: Color,
}

/// A band's region: opacity, outline and fill.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct BandRectMarkStyle {
    /// Region opacity, 0 to 1.
    #[model(local)]
    pub opacity: Fraction,
    /// Outline of the region.
    #[model(absent = "no stroke")]
    pub stroke: Option<AnnotationStrokeStyle>,
    /// Fill color of the region.
    #[model(local)]
    pub fill: Color,
}

/// A box around a note: the band rect plus the inset around the text.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct NoteRectMarkStyle {
    #[flatten]
    pub rect: BandRectMarkStyle,
    /// Space between the note's text and the box drawn around it, in pixels.
    #[model(local)]
    pub padding: Px,
}

/// A circle around a note's anchor: opacity, outline and radius.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AnnotationCircleMarkStyle {
    /// Circle opacity, 0 to 1.
    #[model(local)]
    pub opacity: Fraction,
    /// Outline of the circle.
    pub stroke: AnnotationStrokeStyle,
    /// Radius of the circle, in pixels; `auto` clears the annotated point by the
    /// text gap, at least 6.
    #[model(local)]
    pub radius: Engine<Px, Auto>,
}

/// The line of a rule.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AnnotationRuleMarkStyle {
    /// Rule opacity, 0 to 1.
    #[model(local)]
    pub opacity: Fraction,
    /// Rule stroke style.
    pub stroke: RuleStrokeStyle,
}

/// The marks of a rule.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct RuleAnnotationMarksStyle {
    /// The line of the rule.
    pub rule: AnnotationRuleMarkStyle,
    /// The rule's text.
    pub text: RuleTextMarkStyle,
}

/// The marks of a band.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct BandAnnotationMarksStyle {
    /// The shaded region.
    pub rect: BandRectMarkStyle,
    /// The band's text.
    pub text: BandTextMarkStyle,
}

/// The marks of a note.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct NoteAnnotationMarksStyle {
    /// The note's text.
    pub text: NoteTextMarkStyle,
    /// The connector from the anchor to the text.
    pub line: NoteLineMarkStyle,
    /// The circle around the anchor.
    pub circle: AnnotationCircleMarkStyle,
    /// The box around the note.
    pub rect: NoteRectMarkStyle,
}

/// The marks of a trend.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TrendAnnotationMarksStyle {
    /// The fitted curve.
    pub line: AnnotationLineMarkStyle,
    /// The interval.
    pub area: AnnotationAreaMarkStyle,
    /// The trend's text.
    pub text: TrendTextMarkStyle,
}

/// Rule defaults: any mark leaf left unset falls back to `style.charts.marks`.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct RuleAnnotationStyle {
    /// Marks of a rule.
    #[model(inherit_slot = "Style.charts.marks")]
    pub marks: RuleAnnotationMarksStyle,
}

/// Band defaults: any mark leaf left unset falls back to `style.charts.marks`.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct BandAnnotationStyle {
    /// Marks of a band.
    #[model(inherit_slot = "Style.charts.marks")]
    pub marks: BandAnnotationMarksStyle,
}

/// Note defaults: any mark leaf left unset falls back to `style.charts.marks`.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct NoteAnnotationStyle {
    /// Marks of a note.
    #[model(inherit_slot = "Style.charts.marks")]
    pub marks: NoteAnnotationMarksStyle,
}

/// Trend defaults: any mark leaf left unset falls back to `style.charts.marks`.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TrendAnnotationStyle {
    /// Marks of a trend.
    #[model(inherit_slot = "Style.charts.marks")]
    pub marks: TrendAnnotationMarksStyle,
}

/// Text of a note with no anchor.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(complete)]
pub struct NoteFrameTextStyle {
    /// Where a note with no anchor sits on the plot frame.
    pub position: AnnotationPositionStyle,
}

/// Defaults for a note pinned to the plot frame.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(complete)]
pub struct NoteFrameStyle {
    /// Text of a note with no anchor.
    pub text: NoteFrameTextStyle,
}

/// Theme defaults for notes, plus the placement of a note with no anchor.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct NoteAnnotationThemeStyle {
    #[flatten]
    pub note: NoteAnnotationStyle,
    /// Defaults for a note pinned to the plot frame.
    pub frame: NoteFrameStyle,
}

/// Defaults for a trend's text wrap.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(max_share, gt = 0, le = 1)))]
pub struct TrendLabelStyle {
    /// Most of the plot's width a trend's text may take beside the plot; the
    /// wrap measure shrinks to fit.
    pub max_share: f64,
}

/// Theme defaults for trends, plus the wrap of their text.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TrendAnnotationThemeStyle {
    #[flatten]
    pub trend: TrendAnnotationStyle,
    /// Defaults for a trend's text wrap.
    pub label: TrendLabelStyle,
}

/// Theme defaults for every annotation kind.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(complete)]
pub struct AnnotationsStyle {
    /// Defaults for rules.
    pub rule: RuleAnnotationStyle,
    /// Defaults for bands.
    pub band: BandAnnotationStyle,
    /// Defaults for notes.
    pub note: NoteAnnotationThemeStyle,
    /// Defaults for trends.
    pub trend: TrendAnnotationThemeStyle,
}
