//! `style/theme/marks.py`: the per-mark-type styles shared by every chart family.
//!
//! A `None` that only means "not set at this tier" is a total field here: the
//! patch twin carries unset. A `stroke: StrokeStyle | None` whose `None` is the absent
//! state `no stroke` is `Option<StrokeStyle>` with `absent`. A `format` field is
//! a [`FormatSpec`]; the CSS border shorthand waits for struct alternates in `accept`. Jinja syntax of `template` and `where` is the
//! template pass's check (spec § 8), not a newtype's.

use crate::facet::{Color, FontFamily, FontWeightValue, LabelWhere};
use crate::primitives::{
    BorderStyle, Curve, FontColorStrokeStyle, FormatSpec, LineCap, StrokeStyle,
};
use crate::rule::Auto;
use crate::string_enum;
use crate::style::font::{FontSlant, FontStyle, LetterCase, SymbolShape, TextDecoration};
use crate::unit::Fraction;
use model_core::{Cx, Engine, FromNode, Leaf, Merge, Node, ReadScalar, Scalar, TypeExpr};
use model_derive::Model;

string_enum!(
    /// A color keyword that paints each label in its series' color.
    Series { Series = "series" }
);
string_enum!(
    /// Which marks of a series a value label names.
    LabelAnchor { First = "first", Last = "last", Min = "min", Max = "max" }
);
string_enum!(
    /// Where a bar's value label sits against its bar.
    BarLabelPosition {
        Above = "above",
        Top = "top",
        Middle = "middle",
        MiddleAligned = "middle_aligned",
        Bottom = "bottom",
    }
);
string_enum!(
    /// Where a point or line label sits against its point.
    PointLabelPosition {
        Top = "top",
        Bottom = "bottom",
        Left = "left",
        Right = "right",
        Middle = "middle",
    }
);

/// `labels.visible`: off, every mark, or the listed anchors. `last` alone
/// reads as `[last]`; the list is non-empty and repeats no anchor.
#[derive(Debug, Clone, PartialEq)]
pub enum LabelVisible {
    Off,
    All,
    Anchors(Vec<LabelAnchor>),
}

impl LabelVisible {
    /// The anchors the value names; empty when it names none.
    pub fn anchors(&self) -> &[LabelAnchor] {
        match self {
            Self::Anchors(a) => a,
            _ => &[],
        }
    }

    fn from_anchors(anchors: Vec<LabelAnchor>) -> Result<Self, String> {
        if anchors.is_empty() {
            return Err("List should have at least 1 item after validation, not 0".into());
        }
        let mut seen: Vec<LabelAnchor> = Vec::new();
        for a in &anchors {
            if seen.contains(a) {
                let names: Vec<String> = anchors
                    .iter()
                    .map(|a| format!("'{}'", a.as_str()))
                    .collect();
                return Err(format!(
                    "Value error, labels.visible repeats an anchor: [{}]",
                    names.join(", ")
                ));
            }
            seen.push(*a);
        }
        Ok(Self::Anchors(anchors))
    }
}

impl ReadScalar for LabelVisible {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        if let Ok(Some(b)) = cx.attempt(|cx| bool::read(node, cx)) {
            return Some(if b { Self::All } else { Self::Off });
        }
        if let Ok(Some(s)) = cx.attempt(|cx| String::read(node, cx)) {
            if s == "last" {
                return Some(Self::Anchors(vec![LabelAnchor::Last]));
            }
            cx.invalid(
                node.span,
                "Input should be a boolean, 'last' or a list of anchors",
            );
            return None;
        }
        let list = <Vec<LabelAnchor> as FromNode>::whole(node, cx)?;
        match Self::from_anchors(list) {
            Ok(v) => Some(v),
            Err(e) => {
                cx.invalid(node.span, e);
                None
            }
        }
    }
}

impl FromNode for LabelVisible {
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

impl Leaf for LabelVisible {
    type Whole = LabelVisible;
    type Authored = Option<LabelVisible>;
    type Patch = Option<LabelVisible>;
    const MERGE: Merge = Merge::Override;
    const EXPR: TypeExpr = TypeExpr::Keyed(
        &model_core::Keywords::at_least_items(1),
        &TypeExpr::Either(
            &TypeExpr::OneOf(&["last"]),
            &TypeExpr::Either(
                &TypeExpr::Scalar(Scalar::Bool),
                &TypeExpr::List(&TypeExpr::OneOf(LabelAnchor::VALUES)),
            ),
        ),
    );
}
model_core::build_leaf!(LabelVisible);

/// Text mark font and alignment.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TextMarkStyle {
    /// Text mark font style overrides.
    #[model(inherit_slot = "Style.charts.font")]
    pub font: FontStyle,
    /// Horizontal text alignment for text marks.
    #[model(fill = "center")]
    pub align: String,
}

/// Series-label typography with a compact width-tier size.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(shorthand = "extends")]
pub struct SeriesLabelFontStyle {
    #[flatten]
    pub font: FontStyle,
    /// Series-label font size in pixels on tiny and narrow cards.
    #[model(local)]
    pub compact_size: f64,
    /// Series-label font weight on tiny and narrow cards.
    #[model(local)]
    pub compact_weight: FontWeightValue,
}

/// Typography for any text mark that names a data series, wherever it sits.
///
/// No `align`: alignment is a placement decision owned by the family renderer.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SeriesLabelStyle {
    /// Series label font style overrides; cascade fills missing fields from charts.font.
    #[model(inherit_slot = "Style.charts.font")]
    pub font: SeriesLabelFontStyle,
}

/// Theme slot for one text element of the donut center total.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TotalSlotStyle {
    /// Donut center total element font style overrides.
    #[model(inherit_slot = "Style.charts.font")]
    pub font: FontStyle,
}

/// Theme slot for the donut center value (the number): paint plus its format.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TotalValueSlotStyle {
    #[flatten]
    pub slot: TotalSlotStyle,
    /// How the donut center value is written: a D3 spec or a preset name.
    #[model(absent = "a donut-shape default")]
    pub format: Option<FormatSpec>,
}

/// Donut center total paint: value (the number) and label (the caption).
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TotalStyle {
    /// Style for the donut center value (the number), including its format.
    pub value: TotalValueSlotStyle,
    /// Style for the donut center label (the caption).
    pub label: TotalSlotStyle,
}

/// Default Jinja templates for per-slice pie/donut labels.
///
/// Applied when the chart omits `labels:` on a pie or donut with `theta:`; the
/// resolver picks `with_color` or `no_color` by whether the chart has a color
/// channel. `{value_line}` stands for the slice value in the tooltip's format.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LabelsDefaultTemplate {
    /// Default per-slice label template when the chart has a color binding.
    pub with_color: String,
    /// Default per-slice label template when the chart has no color binding.
    pub no_color: String,
}

/// The pie slice label font. `color` also takes `auto`.
///
/// A copy of `FontStyle` with one retyped field: a flatten cannot override.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(shorthand = "extends")]
#[model(constraint(range(size, gt = 0)))]
pub struct SliceLabelFontStyle {
    /// Name of a `style.fonts` entry this slot starts from.
    #[model(absent = "no named font")]
    pub extends: Option<String>,
    /// Font family or CSS stack.
    pub family: FontFamily,
    /// Text color. 'auto' paints each label in the dark ink of its wedge's color.
    pub color: Engine<Color, Auto>,
    /// Font size in pixels.
    pub size: f64,
    /// How heavy the type is drawn (e.g., 'bold', 400, 700).
    pub weight: FontWeightValue,
    /// Upright or slanted type (normal or italic).
    pub style: FontSlant,
    /// Line drawn on the text (underline, line-through, or none).
    pub decoration: TextDecoration,
    /// Letter-case transform applied at render time.
    pub case: LetterCase,
    /// Line height as a unitless multiple of font size.
    pub line_height: f64,
}

/// Pie labels: typography and positioning offsets for per-slice text.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SliceLabelsStyle {
    /// Radial offset of slice labels from the arc in pixels.
    pub offset: f64,
    /// Line height for slice labels in pixels, per rendered line.
    pub line_height: f64,
    /// Slice label font style overrides; `color` only applies to single-series pies.
    #[model(inherit_slot = "Style.charts.font")]
    pub font: SliceLabelFontStyle,
    /// Default Jinja templates for per-slice labels when template is not authored.
    pub default_template: LabelsDefaultTemplate,
    /// Jinja2 label template. Overrides default_template when authored.
    #[model(absent = "the default template")]
    pub template: Option<String>,
    /// Jinja2 boolean filter; labels only render on rows where this is truthy.
    #[model(absent = "every row labeled")]
    pub r#where: Option<LabelWhere>,
}

/// Background map layer for geo charts.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct BasemapStyle {
    /// Background topo source identifier.
    #[model(absent = "no base layer")]
    pub source: Option<String>,
}

/// The value label font. `color` also takes `series`.
///
/// A copy of `FontStyle` with one retyped field: a flatten cannot override.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(shorthand = "extends")]
#[model(constraint(range(size, gt = 0)))]
pub struct ValueLabelFontStyle {
    /// Name of a `style.fonts` entry this slot starts from.
    #[model(absent = "no named font")]
    pub extends: Option<String>,
    /// Font family or CSS stack.
    pub family: FontFamily,
    /// Text color as a CSS color string; `series` takes each label's series color.
    #[model(fill = "chrome[7]")]
    pub color: Engine<Color, Series>,
    /// Font size in pixels.
    pub size: f64,
    /// How heavy the type is drawn (e.g., 'bold', 400, 700).
    pub weight: FontWeightValue,
    /// Upright or slanted type (normal or italic).
    pub style: FontSlant,
    /// Line drawn on the text (underline, line-through, or none).
    pub decoration: TextDecoration,
    /// Letter-case transform applied at render time.
    pub case: LetterCase,
    /// Line height as a unitless multiple of font size.
    pub line_height: f64,
}

/// The value-label fields every mark shares, before its font.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct MarkLabelsBase {
    /// Show numeric value labels on every mark or on listed anchors (`first`,
    /// `last`, `min`, `max`, per series; a tie labels every tied mark).
    #[model(fill = false)]
    pub visible: LabelVisible,
    /// Column whose text labels each mark; `''` where unlabeled, never NULL.
    #[model(absent = "the chart's y-field")]
    pub field: Option<String>,
    /// Value label format: a name or d3 spec.
    #[model(absent = "the measure axis format")]
    pub format: Option<FormatSpec>,
    /// Horizontal pixel offset for value labels; 'auto' uses the position default.
    #[model(fill = "auto")]
    pub dx: Engine<i32, Auto>,
    /// Vertical pixel offset for value labels; 'auto' uses the position default.
    #[model(fill = "auto")]
    pub dy: Engine<i32, Auto>,
}

/// Shared value-label config for bar/line/point marks.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct MarkLabelsStyle {
    #[flatten]
    pub base: MarkLabelsBase,
    /// Value label font style (color, size, family, etc.).
    pub font: FontStyle,
}

/// Stack total label style for bar marks.
///
/// Draws one label past the topmost segment of each stack, showing its sum.
/// Only takes effect on stacked bar charts.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct BarTotalLabelStyle {
    /// Show stack total labels above each bar stack; 'auto' hides them.
    #[model(fill = "auto")]
    pub visible: Engine<bool, Auto>,
    /// Number format string for stack total labels.
    #[model(absent = "the measure axis format")]
    pub format: Option<FormatSpec>,
    /// Horizontal pixel offset for stack total labels; 'auto' uses the default.
    #[model(fill = "auto")]
    pub dx: Engine<i32, Auto>,
    /// Vertical pixel offset for stack total labels; 'auto' uses the default.
    #[model(fill = "auto")]
    pub dy: Engine<i32, Auto>,
    /// Stack total label font style overrides; cascade fills missing fields from charts.font.
    #[model(inherit_slot = "Style.charts.font")]
    pub font: FontStyle,
}

/// Bar mark value-label config: shared labels plus bar positions.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct BarLabelsStyle {
    #[flatten]
    pub labels: MarkLabelsStyle,
    /// Label position: `above` (past the bar's end), `top` (inside the end),
    /// `middle`, `middle_aligned` (all at one height), `bottom` (inside the start).
    #[model(fill = "top")]
    pub position: BarLabelPosition,
    /// Label each segment of a stacked bar; 'auto' labels them.
    #[model(fill = "auto")]
    pub stacked: Engine<bool, Auto>,
}

/// Point and line mark value-label config.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PointLabelsStyle {
    #[flatten]
    pub base: MarkLabelsBase,
    /// Value label font style (color, size, family, etc.).
    pub font: ValueLabelFontStyle,
    /// Label position relative to the point; 'auto' leaves the spot to the renderer.
    #[model(fill = "auto")]
    pub position: Engine<PointLabelPosition, Auto>,
}

/// Bar mark geometry and stroke. Chart-level bar fields live on the bar chart style.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct BarMarkStyle {
    /// Bar border style (color/width also serve as the bar stroke).
    pub border: BorderStyle,
    /// Padding around bar marks in pixels.
    #[model(fill = 0.0)]
    pub padding: f64,
    /// Bar width in pixels; overrides all other sizing.
    #[model(absent = "sized by the band or gap ladder")]
    pub size: Option<f64>,
    /// Bar width as a fraction of the band step (0 to 1).
    #[model(fill = 0.8)]
    pub band_width: f64,
    /// Target pixel gap between adjacent bars on a continuous scale.
    #[model(fill = 3.0)]
    pub gap: f64,
    /// Narrowest a computed bar width may shrink to on a continuous scale.
    #[model(fill = 4.0)]
    pub min_size: f64,
    /// Thickest a bar may be, in pixels.
    #[model(absent = "uncapped")]
    pub max_size: Option<f64>,
    /// Bar fill opacity (0 to 1); 'auto' uses VL default.
    #[model(fill = "auto")]
    pub opacity: Engine<f64, Auto>,
    /// Value label style for bar marks.
    pub labels: BarLabelsStyle,
    /// Stack total label style. Only takes effect on stacked bar charts.
    pub total_label: BarTotalLabelStyle,
}

/// Line mark stroke, interpolation, and halo. The point mark is `PointMarkStyle`.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(disconnected_width, gt = 0, le = 1)))]
pub struct LineMarkStyle {
    /// Line stroke style.
    pub stroke: StrokeStyle,
    /// Line interpolation curve, one of a fixed set.
    #[model(fill = "linear")]
    pub curve: Curve,
    /// For curve='step' on a categorical x-axis, whether adjacent band plateaus
    /// are joined by vertical jumps or left as disconnected segments.
    #[model(fill = "auto")]
    pub connect: Engine<bool, Auto>,
    /// Stroke line cap for the sub-paths of a disconnected band-step.
    #[model(fill = "butt")]
    pub disconnected_cap: LineCap,
    /// Share of the band width a disconnected band-step plateau spans.
    #[model(fill = 0.9)]
    pub disconnected_width: f64,
    /// Halo stroke width multiplier relative to stroke.width; 0 disables the halo.
    #[model(fill = 2.0)]
    pub halo_multiplier: f64,
    /// Value label style for line marks.
    pub labels: PointLabelsStyle,
}

/// Stacked and streamgraph area recipe: solid fill plus perimeter stroke.
///
/// A model of its own, not a recursive `AreaMarkStyle`.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AreaStackedMarkStyle {
    /// Area fill opacity (0 to 1) for the stacked recipe.
    #[model(fill = 1.0)]
    pub opacity: f64,
    /// Perimeter stroke style for the stacked recipe.
    pub stroke: StrokeStyle,
    /// Halo stroke width multiplier for the stacked recipe; 0 disables the halo.
    #[model(fill = 0.0)]
    pub halo_multiplier: f64,
}

/// Area mark fill opacity and shape. The edge line is `LineMarkStyle`.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AreaMarkStyle {
    /// Area fill opacity (0 to 1).
    #[model(fill = 0.16)]
    pub opacity: Fraction,
    /// Area interpolation curve, applied to both the fill and its edge line.
    #[model(fill = "auto")]
    pub curve: Engine<Curve, Auto>,
    /// Whether to paint an opaque fill backdrop behind the area.
    #[model(fill = true)]
    pub backdrop: bool,
    /// Recipe override applied when the chart is stacked or has a single series.
    pub stacked: AreaStackedMarkStyle,
}

/// Point mark style (data-point markers on scatter, point, line and area charts).
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PointMarkStyle {
    /// Point size in square pixels; 0 disables points on line charts.
    pub size: Engine<f64, Auto>,
    /// Point color; 'auto' inherits the series color.
    #[model(fill = "auto")]
    pub color: Engine<Color, Auto>,
    /// Point shape (e.g. 'circle', 'square'); 'auto' uses VL default.
    #[model(fill = "auto")]
    pub shape: Engine<SymbolShape, Auto>,
    /// Point opacity, 0 to 1.
    #[model(fill = 1.0)]
    pub opacity: f64,
    /// Whether points are filled.
    #[model(fill = true)]
    pub filled: bool,
    /// Point interior fill color; only applied when filled=false.
    #[model(fill = "theme.background")]
    pub fill: Color,
    /// Stroke width in pixels for hollow point rings; 'auto' uses VL default.
    pub stroke_width: Engine<f64, Auto>,
    /// Value label style for point marks.
    pub labels: PointLabelsStyle,
}

/// Pie/donut slice mark: paint and layout only.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SliceMarkStyle {
    /// Arc slice opacity (0 to 1); 'auto' uses the board opacity.
    #[model(fill = "auto")]
    pub opacity: Engine<f64, Auto>,
    /// Angular gap between slices in radians.
    #[model(fill = 0.0)]
    pub gap: f64,
    /// Corner radius of arc slices in pixels.
    #[model(fill = 3.0)]
    pub corner_radius: f64,
    /// Arc slice stroke style.
    pub stroke: StrokeStyle,
    /// Per-slice label style.
    pub labels: SliceLabelsStyle,
}

/// Rule (reference line) mark opacity and stroke.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct RuleMarkStyle {
    /// Mark opacity (0 to 1); 'auto' uses the board opacity.
    #[model(fill = "auto")]
    pub opacity: Engine<Fraction, Auto>,
    /// Mark stroke style.
    pub stroke: FontColorStrokeStyle,
}

/// Rect mark opacity and stroke.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct RectMarkStyle {
    /// Mark opacity (0 to 1); 'auto' uses the board opacity.
    #[model(fill = "auto")]
    pub opacity: Engine<Fraction, Auto>,
    /// Mark stroke style.
    #[model(absent = "no stroke")]
    pub stroke: Option<StrokeStyle>,
}

/// Circle mark opacity and stroke.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct CircleMarkStyle {
    /// Mark opacity (0 to 1); 'auto' uses the board opacity.
    #[model(fill = 1.0)]
    pub opacity: Engine<Fraction, Auto>,
    /// Mark stroke style.
    #[model(local, absent = "no stroke")]
    pub stroke: Option<StrokeStyle>,
}

/// Geoshape (choropleth) mark fill and boundary stroke.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct GeoshapeMarkStyle {
    /// Neutral geoshape fill color.
    pub fill: Color,
    /// Geoshape boundary stroke style.
    #[model(absent = "no stroke")]
    pub stroke: Option<StrokeStyle>,
}

/// Global mark defaults: one style per Vega-Lite mark type (`text` is the renamed `label`).
#[derive(Debug, Clone, PartialEq, Model)]
pub struct GlobalMarksStyle {
    /// Global bar mark defaults.
    pub bar: BarMarkStyle,
    /// Global line mark defaults.
    pub line: LineMarkStyle,
    /// Global area mark defaults.
    pub area: AreaMarkStyle,
    /// Global point mark defaults.
    pub point: PointMarkStyle,
    /// Global slice (arc/pie) mark defaults.
    pub slice: SliceMarkStyle,
    /// Global text mark defaults.
    pub text: TextMarkStyle,
    /// Global rule mark defaults.
    pub rule: RuleMarkStyle,
    /// Global rect mark defaults.
    pub rect: RectMarkStyle,
    /// Global circle mark defaults.
    pub circle: CircleMarkStyle,
    /// Global geoshape mark defaults.
    pub geoshape: GeoshapeMarkStyle,
}

/// Subtitle font style for chart subtitles (table, spark_bar).
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SubtitleStyle {
    /// Subtitle font style; theme populates font.size directly.
    pub font: FontStyle,
}
