//! `style/theme/axis.py`: axis, scale and tooltip styles.
//!
//! The same models serve the `axis` baseline and the per-role (`axis_x`,
//! `axis_y`) and per-scale (`axis_quantitative`, `axis_band`) slots, whose
//! declared specificity lists render walks leaf by leaf, so every slot and
//! inherit link is kept as Python has it.
//!
//! A `None` that only means "not set at this tier" is a total field here; the
//! patch twin carries unset. A field whose `None` is a state of its own is
//! `Option<T>` with `absent`. Python marks `Absent` on the baseline `axis` only;
//! on a field only a role or scale slot carries (the x-axis `values`, a scale's
//! `round`), its docstring names the `None` state and the spine states it.
//! A `format` is a [`FormatSpec`]: a name or spec string, or a `FormatConfig` block that a
//! layer replaces whole.
//!
//! A subclass that overrides a field cannot be `#[flatten]` composition (the key
//! would repeat), so those are models of their own and share the unchanged
//! fields through [`TicksRest`], which has no Python counterpart.
//!
//! Dissolved validators: `AxisXStyle._yaml_null_is_null_fill_mode` (`fill: null`
//! reads as the `null` mode) is a parse error on the spine (D-07; the built-in
//! themes quote `"null"`); the `_coerce_list_to_tuple` before-validator of
//! `domain` is a fixed-arity read. `NullHint` on the axis title is a message
//! template (spec § 6), not an attribute.

use crate::facet::{Color, FormatName};
use crate::primitives::FormatSpec;
use crate::rule::Auto;
use crate::string_enum;
use crate::style::board::PaddingStyle;
use crate::style::font::FontStyle;
use crate::unit::Fraction;
use model_core::{
    Cx, Engine, FromNode, Leaf, Merge, Node, ReadScalar, Scalar, Spell, Tri, TypeExpr,
};
use model_derive::Model;
use serde_json::Value;

string_enum!(
    /// A calendar grain: Vega-Lite's `timeUnit` values other than `auto`.
    TimeUnit {
        Year = "year",
        YearQuarter = "yearquarter",
        YearMonth = "yearmonth",
        YearWeek = "yearweek",
        YearMonthDate = "yearmonthdate",
        MonthOfYear = "monthofyear",
        DayOfWeek = "dayofweek",
        DayOfMonth = "dayofmonth",
        DayOfYear = "dayofyear",
        HourOfDay = "hourofday",
        None = "none",
    }
);
string_enum!(
    /// Horizontal text alignment of an axis title or label. `inward` and `outward`
    /// resolve against the axis's own left or right edge.
    TextAlign {
        Left = "left",
        Right = "right",
        Center = "center",
        Inward = "inward",
        Outward = "outward",
    }
);
string_enum!(
    /// Scale type override of a continuous scale.
    ScaleType {
        Linear = "linear",
        Log = "log",
        Pow = "pow",
        Sqrt = "sqrt",
        Symlog = "symlog",
        Temporal = "temporal",
    }
);
string_enum!(
    /// Scale type of a bucketed-time x-axis.
    XAxisType { Ordinal = "ordinal", Temporal = "temporal" }
);
string_enum!(
    /// How a synthesized missing time bucket is filled.
    FillMode {
        Null = "null",
        Zero = "zero",
        Linear = "linear",
        StepAfter = "step-after",
        StepBefore = "step-before",
        StepCenter = "step-center",
        Curve = "curve",
    }
);
string_enum!(
    /// Where the x-axis sits.
    XAxisPosition { Top = "top", Bottom = "bottom" }
);
string_enum!(
    /// Which side the y-axis sits on.
    YAxisPosition { Left = "left", Right = "right" }
);
string_enum!(
    /// Where y-axis labels sit against their gridlines.
    AxisYLabelPosition { BesideGridline = "beside_gridline", AboveGridline = "above_gridline" }
);
string_enum!(
    /// How the hovered row of a multi-row tooltip is marked.
    ActiveMarker { Fill = "fill", Triangle = "triangle" }
);

macro_rules! scalar_leaf {
    ($t:ty, $facet:literal, $kind:ident) => {
        scalar_leaf!($t, TypeExpr::Facet($facet, Scalar::$kind));
    };
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

macro_rules! non_empty_list {
    ($(#[$m:meta])* $name:ident, $item:ty) => {
        $(#[$m])*
        #[derive(Debug, Clone, PartialEq)]
        pub struct $name(pub Vec<$item>);

        impl ReadScalar for $name {
            fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
                let items = <Vec<$item> as FromNode>::whole(node, cx)?;
                if items.is_empty() {
                    cx.invalid(
                        node.span,
                        "List should have at least 1 item after validation, not 0",
                    );
                    return None;
                }
                Some(Self(items))
            }
        }

        impl model_core::Spell for $name {
            fn spell(&self) -> model_core::serde_json::Value {
                model_core::Spell::spell(&self.0)
            }
        }

        impl FromNode for $name {
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

        impl Leaf for $name {
            type Whole = $name;
            type Authored = Option<$name>;
            type Patch = Option<$name>;
            const MERGE: Merge = Merge::Override;
            const EXPR: TypeExpr = TypeExpr::Keyed(
                &model_core::Keywords::at_least_items(1),
                &TypeExpr::List(&<$item as Leaf>::EXPR),
            );
        }
        model_core::build_leaf!($name);
    };
}
pub(crate) use non_empty_list;

/// `bound` and `flush`: a switch, or a pixel distance.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum SwitchOrPixels {
    Switch(bool),
    Pixels(f64),
}

impl ReadScalar for SwitchOrPixels {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        if let Ok(Some(b)) = cx.attempt(|cx| bool::read(node, cx)) {
            return Some(Self::Switch(b));
        }
        f64::read(node, cx).map(Self::Pixels)
    }
}
impl Spell for SwitchOrPixels {
    fn spell(&self) -> Value {
        match self {
            Self::Switch(b) => b.spell(),
            Self::Pixels(p) => p.spell(),
        }
    }
}
scalar_leaf!(
    SwitchOrPixels,
    TypeExpr::Either(
        &TypeExpr::Scalar(Scalar::Bool),
        &TypeExpr::Scalar(Scalar::Float)
    )
);

/// A tick value of a scale, or a date a label keeps: any scalar the board writes.
#[derive(Debug, Clone, PartialEq)]
pub struct TickValue(Value);

impl TickValue {
    pub fn as_json(&self) -> &Value {
        &self.0
    }
}

impl ReadScalar for TickValue {
    fn read(node: &Node, _: &mut Cx) -> Option<Self> {
        Some(Self(node.to_json()))
    }
}
impl Spell for TickValue {
    fn spell(&self) -> Value {
        self.0.clone()
    }
}
scalar_leaf!(TickValue, "tick_value", Json);

non_empty_list!(
    /// Descending tilt angles, at least one.
    TiltIncrements,
    f64
);
non_empty_list!(
    /// Dates to keep label text on, at least one.
    LabelDates,
    TickValue
);

/// One bound of a scale domain: a number, or an ISO-8601 date text. A switch
/// is refused, where a lax reader would read `true` as `1`.
#[derive(Debug, Clone, PartialEq)]
pub enum DomainBound {
    Number(f64),
    Text(String),
}

/// An explicit `[low, high]` scale domain: exactly two bounds, in order.
#[derive(Debug, Clone, PartialEq)]
pub struct ScaleDomain {
    pub low: DomainBound,
    pub high: DomainBound,
}

fn bound(v: &Value) -> Option<DomainBound> {
    match v {
        Value::Number(n) => n.as_f64().map(DomainBound::Number),
        Value::String(s) => Some(DomainBound::Text(s.clone())),
        _ => None,
    }
}

impl ReadScalar for ScaleDomain {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        let Value::Array(items) = node.to_json() else {
            cx.mismatch(node, "a list of two bounds");
            return None;
        };
        let [low, high] = items.as_slice() else {
            let side = if items.len() < 2 {
                "at least"
            } else {
                "at most"
            };
            cx.invalid(
                node.span,
                format!(
                    "Tuple should have {side} 2 items after validation, not {}",
                    items.len()
                ),
            );
            return None;
        };
        match (bound(low), bound(high)) {
            (Some(low), Some(high)) => Some(Self { low, high }),
            _ => {
                cx.mismatch(node, "two numbers or date strings");
                None
            }
        }
    }
}
impl Spell for ScaleDomain {
    fn spell(&self) -> Value {
        let bound = |b: &DomainBound| match b {
            DomainBound::Number(n) => n.spell(),
            DomainBound::Text(t) => t.spell(),
        };
        Value::Array(vec![bound(&self.low), bound(&self.high)])
    }
}
const DOMAIN_BOUND: TypeExpr = TypeExpr::Either(
    &TypeExpr::Either(
        &TypeExpr::Scalar(Scalar::Int),
        &TypeExpr::Scalar(Scalar::Float),
    ),
    &TypeExpr::Scalar(Scalar::Str),
);
scalar_leaf!(
    ScaleDomain,
    TypeExpr::Tuple(&[&DOMAIN_BOUND, &DOMAIN_BOUND])
);

/// The y-axis `mirror`: `false` (as good as unset), the primary axis's label drawn on both edges, or
/// the mirrored edge relabeled by `format` or `expr`.
#[derive(Debug, Clone, PartialEq)]
pub enum AxisMirror {
    Off,
    Verbatim,
    Relabel(<AxisMirrorStyle as Leaf>::Whole),
}

impl ReadScalar for AxisMirror {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        if let Ok(Some(b)) = cx.attempt(|cx| bool::read(node, cx)) {
            return Some(if b { Self::Verbatim } else { Self::Off });
        }
        let style = <AxisMirrorStyle as FromNode>::whole(node, cx)?;
        let message = match (
            matches!(style.format, Tri::Value(_)),
            matches!(style.expr, Tri::Value(_)),
        ) {
            (true, true) => "mirror takes format or expr, not both — expr fully controls the label text (call format() inside it if needed).",
            (false, false) => "empty mirror override — set format or expr to relabel the mirrored edge, or use `mirror: true` to mirror the primary axis's label verbatim.",
            _ => return Some(Self::Relabel(style)),
        };
        cx.invalid(node.span, format!("Value error, {message}"));
        None
    }
}

impl Spell for AxisMirror {
    fn spell(&self) -> Value {
        match self {
            Self::Off => false.into(),
            Self::Verbatim => true.into(),
            Self::Relabel(style) => style.spell(),
        }
    }
}

impl FromNode for AxisMirror {
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

impl Leaf for AxisMirror {
    type Whole = AxisMirror;
    type Authored = Option<AxisMirror>;
    type Patch = Option<AxisMirror>;
    const MERGE: Merge = Merge::Override;
    const EXPR: TypeExpr = TypeExpr::Either(
        &TypeExpr::Ref(
            "AxisMirrorStyle",
            <AxisMirrorStyle as model_core::Model>::info,
        ),
        &TypeExpr::Scalar(Scalar::Bool),
    );
}
model_core::build_leaf!(AxisMirror);

/// Two-switch overlap strategy enablement for x-axis labels, applied in fixed
/// order: skip, then tilt. On bucketed temporal axes, skip advances label
/// visibility by one meaningful calendar period. Categorical axes never skip,
/// since removing a category label loses domain information; they only tilt.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AxisLabelOverlapConfig {
    /// Walk the tilt increments and apply the first angle that fits.
    #[model(fill = true)]
    pub tilt: bool,
    /// Thin temporal labels; ignored on categorical axes.
    #[model(fill = true)]
    pub skip: bool,
}

/// Threshold-rule color, width and visibility.
///
/// Styles the heavy rule drawn at a quantitative axis's meaningful crossing
/// point: zero on most quantitative axes, 1.0 on a ratio or percent axis.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AxisGridThresholdStyle {
    /// Show the threshold rule.
    #[model(fill = true)]
    pub visible: bool,
    /// Color of the threshold rule.
    pub color: Color,
    /// Width of the threshold rule in pixels.
    #[model(fill = 2.0)]
    pub width: f64,
}

/// Torn-baseline wave: the plot edge nearest an off-screen zero.
///
/// When a value axis excludes zero, the plot edge nearest zero is drawn as a
/// wave in the gridline ink so it cannot be read as a floor at zero. Line
/// charts draw it on the y-axis and ranged bars on their value axis; other
/// chart types ignore it.
#[derive(Debug, Clone, PartialEq, Model)]
// One `range` names one field, so two bounds are two constraints.
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(range(amplitude, gt = 0)),
    constraint(range(wavelength, ge = 1))
)]
pub struct AxisGridTornBaselineStyle {
    /// Draw the torn baseline.
    #[model(fill = false)]
    pub enabled: bool,
    /// Wave height in pixels, edge to crest.
    #[model(fill = 3.5)]
    pub amplitude: f64,
    /// Length of one full wave in pixels.
    #[model(fill = 30.0)]
    pub wavelength: f64,
    /// 0 draws a smooth sine, 1 a sharp zigzag.
    #[model(fill = 1.0)]
    pub sharpness: Fraction,
}

/// Grid line style for every axis variant.
///
/// `threshold` styles the heavy rule at a quantitative axis's crossing point and
/// has no visible effect on a categorical or band axis.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct BaseAxisGridStyle {
    /// Show grid lines.
    pub visible: bool,
    /// Grid line opacity.
    #[model(fill = 1.0)]
    pub opacity: f64,
    /// Grid line width in pixels.
    pub width: f64,
    /// Grid line color.
    pub color: Color,
    /// Dash pattern for grid lines. Each axis variant owns its dash.
    #[model(absent = "solid line", local)]
    pub dash: Option<Vec<f64>>,
    /// Threshold-rule gridline style.
    pub threshold: AxisGridThresholdStyle,
    /// Torn-baseline wave style.
    pub torn_baseline: AxisGridTornBaselineStyle,
}

/// Axis domain (baseline) line style.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AxisLineStyle {
    /// Show the axis domain line.
    #[model(fill = false)]
    pub visible: bool,
    /// Domain line width in pixels.
    #[model(fill = 1.0)]
    pub width: f64,
    /// Domain line color.
    pub color: Color,
}

/// Every tick field except `visible`, which axis_x widens.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TicksRest {
    /// Tick color.
    pub color: Color,
    /// Tick length in pixels.
    #[model(fill = 6.0)]
    pub length: f64,
    /// Tick stroke width in pixels.
    pub width: f64,
    /// Pixel offset of ticks from their default position; 'auto' means no offset.
    pub offset: Engine<f64, Auto>,
    /// Target number of axis ticks, or 'auto' for the engine's own sparse ladder:
    /// a target everywhere, never an exact count. On the measure axis the
    /// renderer computes a round-numbered ladder of at most this many ticks; on
    /// axis_x it passes through as Vega-Lite's tickCount. An ordinal axis_x has
    /// no tick-count concept and ignores it. Each axis variant sets its own.
    #[model(absent = "no tick ladder is baked; Vega-Lite picks the ticks", local)]
    pub count: Option<Engine<i64, Auto>>,
    /// Tick interval on axis_x. Alongside `time_unit` it is a multiple of that
    /// calendar grain; on its own it is a numeric interval for a quantitative
    /// axis and acts as a floor rather than a fixed ladder. A bare step on an
    /// axis that is not quantitative is an error; axis_y rejects step entirely.
    #[model(absent = "no interval pinned", local)]
    pub step: Option<i64>,
}

/// Tick marks on an axis: visibility, color, size and cadence.
///
/// Every axis slot uses this shape except axis_x, which adds `time_unit`.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AxisTicksStyle {
    /// Show axis ticks.
    pub visible: bool,
    #[flatten]
    pub rest: TicksRest,
}

/// axis_x ticks: adds the calendar unit that anchors a step cadence.
///
/// Distinct from `DimensionLabelStyle.time_unit` (label cadence) and
/// `AxisXStyle.time_unit` (bucketing grain). A bare `step` is not rejected: it
/// is the quantitative-axis cadence lever, and whether the axis resolves to
/// quantitative, temporal or ordinal is data-dependent.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(at_most_one_of(time_unit, count, auto(count),
        message = "ticks.time_unit and ticks.count cannot both be set — they are two different ways to express tick cadence. Use ticks.time_unit/step for a step-anchored cadence (every N years/quarters/months), or ticks.count for VL's advisory tick-count hint, not both."
    )))]
pub struct DimensionTicksStyle {
    /// Show axis ticks. 'auto' shows the tick only where the gridlines cannot
    /// reach all the way to the labels on their own.
    pub visible: Engine<bool, Auto>,
    #[flatten]
    pub rest: TicksRest,
    /// Step-anchored tick cadence unit.
    #[model(absent = "no step-anchored ticks")]
    pub time_unit: Option<Engine<TimeUnit, Auto>>,
}

/// Axis title typography, deliberately thin: a title is one short static string,
/// not a per-tick label stream, so it has no overlap strategy or passthrough knobs.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AxisTitleStyle {
    /// Axis title font style overrides.
    #[model(inherit_slot = "Style.charts.font")]
    pub font: FontStyle,
    /// Padding between axis title and labels in pixels.
    #[model(fill = 10.0)]
    pub padding: f64,
    /// Title rotation angle in degrees; 'auto' uses Vega-Lite's default.
    #[model(fill = "auto")]
    pub angle: Engine<f64, Auto>,
    /// Horizontal text alignment of the title. 'inward' and 'outward' resolve
    /// against the axis's own left or right edge, as `label.align` does, and are
    /// a no-op on an axis with no left or right edge.
    #[model(fill = "auto")]
    pub align: Engine<TextAlign, Auto>,
    /// Show the axis title. 'auto' shows it only when the chart authors an
    /// x_label or y_label. An explicit true or false from any tier wins.
    pub visible: Engine<bool, Auto>,
}

/// Axis label: font, padding and Vega-Lite passthrough.
///
/// The universal base: fields meaningful on both dimension and measure axes.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AxisLabelStyle {
    /// Axis element font style overrides.
    #[model(inherit_slot = "Style.charts.font")]
    pub font: FontStyle,
    /// Padding between axis labels and ticks in pixels.
    #[model(fill = 6.0)]
    pub padding: f64,
    /// Maximum label width in pixels; 'auto' uses Vega-Lite's default (180px).
    #[model(fill = "auto")]
    pub max_width: Engine<f64, Auto>,
    /// Label rotation angle in degrees; 'auto' uses Vega-Lite's default.
    #[model(fill = "auto")]
    pub angle: Engine<f64, Auto>,
    /// Horizontal text alignment of labels. 'inward' hugs the plot (left on a
    /// left axis, right on a right axis); 'outward' hugs away from it. Both are a
    /// no-op on an axis with no left or right edge. 'auto' uses Vega-Lite's
    /// default, except that a right-edge quantitative axis with a number format
    /// and a tabular label font decimal-aligns its labels.
    #[model(fill = "auto")]
    pub align: Engine<TextAlign, Auto>,
    /// Label overlap strategy enablement; individual switches may be authored
    /// alone while the others inherit.
    pub overlap: AxisLabelOverlapConfig,
    /// Minimum pixel gap between labels.
    #[model(fill = 16.0)]
    pub min_gap: f64,
    /// Show axis labels; 'auto' uses Vega-Lite's default (labels shown).
    #[model(fill = "auto")]
    pub visible: Engine<bool, Auto>,
    /// Custom Vega expression for label text. Read on both axes.
    #[model(absent = "smart temporal defaults")]
    pub expr: Option<String>,
    /// Hide labels that overflow the axis range; 'auto' uses Vega-Lite's default.
    #[model(fill = "auto")]
    pub bound: Engine<SwitchOrPixels, Auto>,
    /// Align first and last label flush with the scale range; 'auto' uses
    /// Vega-Lite's default.
    #[model(fill = "auto")]
    pub flush: Engine<SwitchOrPixels, Auto>,
    /// Pixel offset of the label from its tick anchor; 'auto' uses Vega-Lite's default.
    #[model(fill = "auto")]
    pub offset: Engine<f64, Auto>,
    /// Tick value format.
    #[model(absent = "no format emitted")]
    pub format: Option<FormatSpec>,
}

/// axis_y label: [`AxisLabelStyle`] plus the y-axis-only fields.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AxisYLabelStyle {
    #[flatten]
    pub label: AxisLabelStyle,
    /// Where y-axis labels sit against their gridlines: `beside_gridline` centers each
    /// label on the end of its gridline; `above_gridline` sets it just above the
    /// gridline, with the gridline running under it. Applies only where value
    /// gridlines are horizontal: no effect on a band y axis, a horizontal bar,
    /// rotated labels, or when gridlines are hidden.
    #[model(absent = "left to the theme")]
    pub position: Option<AxisYLabelPosition>,
}

/// axis_x label: [`AxisLabelStyle`] plus the dimension-only fields.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(value_in(clock, 24, 12)))]
pub struct DimensionLabelStyle {
    #[flatten]
    pub label: AxisLabelStyle,
    /// Label cadence for temporal axes; unset follows the axis's `time_unit`,
    /// except yearweek, which labels by month.
    #[model(absent = "follows the axis time_unit")]
    pub time_unit: Option<Engine<TimeUnit, Auto>>,
    /// Sub-day clock for a continuous temporal x-axis: 24 for the unambiguous
    /// 24-hour clock, 12 for the 12-hour clock with the Noon/Midnight vocabulary.
    #[model(absent = "left to the theme")]
    pub clock: Option<u32>,
    /// Descending tilt angles for label overlap on discrete x-axes. The picker
    /// walks the list and takes the first angle whose labels fit; the last entry
    /// is the fall-through. Unset disables tilt.
    #[model(absent = "tilt is disabled")]
    pub tilt_increments: Option<TiltIncrements>,
    /// Dates to keep label text on, chosen from among the axis's ticks: a
    /// filter, not a tick list. Every other tick keeps its position and
    /// gridline but has its label blanked. Temporal x-axes only.
    #[model(absent = "every tick is labeled")]
    pub values: Option<LabelDates>,
}

/// Log-scale-only parameter.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ScaleLogStyle {
    /// Log base; only meaningful with type: log.
    #[model(absent = "Vega-Lite's default (10)")]
    pub base: Option<f64>,
}

/// Power-scale-only parameter.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ScalePowStyle {
    /// Power exponent; only meaningful with type: pow.
    #[model(absent = "Vega-Lite's default")]
    pub exponent: Option<f64>,
}

/// Symlog-scale-only parameter.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ScaleSymlogStyle {
    /// Symlog constant; only meaningful with type: symlog.
    #[model(absent = "Vega-Lite's default")]
    pub constant: Option<f64>,
}

/// Continuous-scale-only config: type, domain, zero baseline, log/pow/symlog params.
///
/// The type-dependent rules span two cascade layers (a theme sets the log type,
/// a chart sets only `log.base`), so they are checked on the folded value.
#[derive(Debug, Clone, PartialEq, Model)]
// One `range` or `forbids` names one field, so several are several constraints.
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(items_match(
        domain,
        pattern = r"^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])([T ]([01][0-9]|2[0-3]):[0-5][0-9](:[0-5][0-9](\.[0-9]+)?)?(Z|[+-]([01][0-9]|2[0-3]):[0-5][0-9])?)?$",
        when(is_in(type, "temporal")),
        message = "scale.domain element {item!r} is not an ISO-8601 date/datetime string, but scale.type: temporal requires both domain bounds to be dates (e.g. '1955-01-01')."
    )),
    constraint(forbids("log.base", when(not_in(type, "log")),
        message = "scale.continuous.log.base is only meaningful with type: log — set axis_y.scale.continuous.type: log, or remove log.base."
    )),
    constraint(forbids("pow.exponent", when(not_in(type, "pow")),
        message = "scale.continuous.pow.exponent is only meaningful with type: pow — set axis_y.scale.continuous.type: pow, or remove pow.exponent."
    )),
    constraint(forbids("symlog.constant", when(not_in(type, "symlog")),
        message = "scale.continuous.symlog.constant is only meaningful with type: symlog — set axis_y.scale.continuous.type: symlog, or remove symlog.constant."
    )),
    constraint(forbids(zero, when(is_in(type, "log"), is_in(zero, true)),
        message = "scale.continuous.type: log is incompatible with zero: true — a log domain cannot include 0. Remove zero or drop the log scale."
    )),
    constraint(range(domain, gt = 0, when(is_in(type, "log")),
        message = "scale.continuous.type: log requires a positive domain — a log scale is undefined at and below 0, got domain {domain}. Use positive bounds or drop the log scale."
    )),
    constraint(range("log.base", gt = 1,
        message = "scale.continuous.log.base must be greater than 1, got {log.base}. A base at or below 1 flips or flattens the scale's decades, so Vega-Lite's nice domain no longer covers the data."
    ))
)]
pub struct ScaleContinuousStyle {
    /// Force the zero baseline: true or false pins it; "auto" runs the smart-zero
    /// heuristic; unset passes through to Vega-Lite.
    #[model(absent = "no zero pin: the smart-zero bake or Vega-Lite's default decides")]
    pub zero: Option<Engine<bool, Auto>>,
    /// Scale type override; a bar rejects 'log', so use 'symlog'. 'temporal' is
    /// an escape hatch for a cartesian x-axis: it forces a continuous temporal
    /// scale instead of the inferred ordinal bucketed type, which an authored
    /// `domain` of ISO dates needs before it can extend past the data.
    #[model(absent = "inferred from the field type")]
    pub r#type: Option<ScaleType>,
    /// Explicit [low, high] scale domain, both bounds pinned. With
    /// `type: temporal` both are ISO-8601 date or datetime strings.
    #[model(absent = "Vega-Lite determines it from the data")]
    pub domain: Option<ScaleDomain>,
    /// Log-scale param.
    #[model(absent = "no log param")]
    pub log: Option<ScaleLogStyle>,
    /// Power-scale param.
    #[model(absent = "no pow param")]
    pub pow: Option<ScalePowStyle>,
    /// Symlog-scale param.
    #[model(absent = "no symlog param")]
    pub symlog: Option<ScaleSymlogStyle>,
}

/// Universal and continuous-only scale config, for `axis_y.scale` and as the
/// base of [`XScaleStyle`]. Band, point, quantize and mark_size config is not
/// authorable; Vega-Lite's own defaults apply.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(headroom, ge = 0)))]
pub struct BaseScaleStyle {
    /// Round scale outputs to the nearest integer.
    #[model(absent = "Vega-Lite's default (no rounding)")]
    pub round: Option<bool>,
    /// Clamp values to the scale domain.
    #[model(absent = "Vega-Lite's default (no clamping)")]
    pub clamp: Option<bool>,
    /// Round the axis domain to nice values.
    #[model(absent = "Vega-Lite's default")]
    pub nice: Option<bool>,
    /// Unified padding shortcut, which Vega-Lite dispatches per scale type:
    /// pixels for continuous scales, a fraction (0..1) for band and point.
    #[model(absent = "Vega-Lite's default")]
    pub padding: Option<f64>,
    /// Fractional breathing room at measure-axis edges, exact (never
    /// nice-rounded). Zero-anchored axes: domain_max = data_max * (1 + headroom).
    /// Zoomed axes expand both edges by headroom * span. 0 disables it. Ignored
    /// with an explicit `domain` or `stack: normalize`.
    #[model(absent = "no headroom override")]
    pub headroom: Option<f64>,
    /// Explicit tick values for the scale. Not `labels.values`, the
    /// dimension-only label filter.
    #[model(absent = "Vega-Lite's tick values")]
    pub values: Option<Vec<TickValue>>,
    /// Continuous-scale overrides.
    #[model(absent = "no continuous override")]
    pub continuous: Option<ScaleContinuousStyle>,
}

/// Scale config for axis_x only: adds `x_reverse`, structurally absent on axis_y.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct XScaleStyle {
    #[flatten]
    pub base: BaseScaleStyle,
    /// Reverse the x-axis scale direction.
    #[model(absent = "no reversal")]
    pub x_reverse: Option<bool>,
}

/// Typography for one tooltip slot (label or value).
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TooltipSlotStyle {
    /// Font overrides for this tooltip slot (color, weight).
    #[model(inherit_slot = "Style.charts.font")]
    pub font: FontStyle,
}

/// Tooltip box border.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TooltipBorderStyle {
    /// Border color.
    pub color: Color,
    /// Border width in pixels.
    pub width: f64,
    /// Border corner radius in pixels.
    pub radius: f64,
}

/// Tooltip drop-shadow toggle.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TooltipShadowStyle {
    /// Show a drop shadow on the tooltip box.
    pub visible: bool,
}

/// Series color swatch in the tooltip: the mark-colored chip beside each series row.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TooltipSwatchStyle {
    /// Swatch edge length in pixels (square).
    pub size: f64,
    /// Swatch corner radius in pixels (0 is square, about half of size a circle).
    pub radius: f64,
}

/// Tooltip box style: every cascade key for the hover bubble.
#[derive(Debug, Clone, PartialEq, Model)]
// One `range` names one field, so two bounds are two constraints.
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(range(max_rows, ge = 1)),
    constraint(range(group_limit, ge = 1))
)]
pub struct TooltipStyle {
    /// Default tooltip value format.
    pub format: FormatName,
    /// Tooltip background color.
    pub background: Color,
    /// Tooltip line-height multiplier.
    pub line_height: f64,
    /// Maximum tooltip width in pixels.
    pub max_width: f64,
    /// Gap in pixels between the label and value columns.
    pub gap: f64,
    /// Tooltip font overrides.
    #[model(inherit_slot = "Style.charts.font")]
    pub font: FontStyle,
    /// Tooltip inner padding (four sides in pixels).
    pub padding: PaddingStyle,
    /// Label-column font overrides.
    pub label: TooltipSlotStyle,
    /// Value-column font overrides.
    pub value: TooltipSlotStyle,
    /// Tooltip border.
    pub border: TooltipBorderStyle,
    /// Tooltip drop shadow.
    pub shadow: TooltipShadowStyle,
    /// Series color swatch size and shape.
    pub swatch: TooltipSwatchStyle,
    /// How the hovered row of a multi-row (x-unified) tooltip is marked: 'fill'
    /// tints the row background; 'triangle' draws an edge-flush wedge in the
    /// box's left padding.
    pub active_marker: ActiveMarker,
    /// Series rows a multi-row tooltip shows before folding the rest into '+N'.
    /// One series past the cap shows as its own row instead of '+1'.
    pub max_rows: u32,
    /// Series count at one x above which the tooltip shows only the hovered
    /// series and its total instead of every series.
    pub group_limit: u32,
}

/// Per-edge label override for the mirrored `axis_y.mirror` ghost axis.
///
/// Mirror's value is one shared y-scale, so ticks align on both edges; this
/// carries only the label presentation needed to relabel the mirrored edge,
/// never a scale or tick override, which would defeat the alignment.
#[derive(Debug, Clone, PartialEq, Model)]
// Vega-Lite ignores `format` whenever a label expression is present, so both
// set makes `format` dead config; an empty object overrides nothing.
#[model(
    constraint(at_most_one_of(format, expr,
        message = "mirror takes format or expr, not both — expr fully controls the label text (call format() inside it if needed)."
    )),
    constraint(at_least_one_of(format, expr,
        message = "empty mirror override — set format or expr to relabel the mirrored edge, or use `mirror: true` to mirror the primary axis's label verbatim."
    ))
)]
pub struct AxisMirrorStyle {
    /// Tick value format for the mirrored edge.
    #[model(absent = "reuses the primary axis's format")]
    pub format: Option<FormatSpec>,
    /// Custom Vega expression for the mirrored edge's label text.
    #[model(absent = "reuses the primary axis's label expression")]
    pub expr: Option<String>,
}

/// Universal axis surface: grid, line, ticks, labels, title and scale.
///
/// Channel-agnostic: the baseline `axis` slot and the base of the y, quantitative
/// and band variants. There is no `position` here; axis_x and axis_y declare
/// their own and the scale-type slots have none.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct BaseAxisStyle {
    /// Grid line style for this axis.
    pub grid: BaseAxisGridStyle,
    /// Domain line style for this axis.
    pub line: AxisLineStyle,
    /// Tick mark style for this axis.
    pub ticks: AxisTicksStyle,
    /// Axis label style.
    pub labels: AxisLabelStyle,
    /// Axis title style.
    pub title: AxisTitleStyle,
    /// Per-axis scale overrides. Each axis variant owns its scale config.
    #[model(absent = "no scale override", local)]
    pub scale: Option<BaseScaleStyle>,
}

/// Dimension (category or time) axis style. Theme slot: axis_x.
///
/// Overrides ticks, labels and scale with their dimension variants and adds the
/// x-only fields. A model of its own, not `BaseAxisStyle` plus fields: the
/// overridden keys would repeat.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(fiscal_year_start_month, ge = 1, le = 12)))]
pub struct AxisXStyle {
    /// Grid line style for this axis.
    pub grid: BaseAxisGridStyle,
    /// Domain line style for this axis.
    pub line: AxisLineStyle,
    /// Dimension axis tick style.
    pub ticks: DimensionTicksStyle,
    /// Dimension axis label style.
    pub labels: DimensionLabelStyle,
    /// Axis title style.
    pub title: AxisTitleStyle,
    /// X-axis scale overrides, including `x_reverse`.
    #[model(absent = "no scale override", local)]
    pub scale: Option<XScaleStyle>,
    /// X-axis position.
    #[model(absent = "Vega-Lite's default (bottom)", local)]
    pub position: Option<XAxisPosition>,
    /// Time-unit bucketing for temporal x-axes. 'none' disables bucketing
    /// (continuous temporal).
    #[model(absent = "auto-detected from the data", local)]
    pub time_unit: Option<Engine<TimeUnit, Auto>>,
    /// Opt-in scale type for bucketed-time x-axes; ordinal when the time unit
    /// resolves to a bucketed calendar grain.
    #[model(absent = "inferred from the time unit grain", local)]
    pub r#type: Option<Engine<XAxisType, Auto>>,
    /// Fill for synthesized missing-bucket rows: null, zero, linear, step-after,
    /// step-before, step-center (Looker step) or curve (smoothstep).
    #[model(local)]
    pub fill: FillMode,
    /// Calendar month (1 is January) that opens a fiscal year or quarter for
    /// year and yearquarter bucketing; 1 is the calendar convention. A non-default
    /// value always wins over `axis_x.type: temporal` for those grains, since
    /// Vega-Lite's timeUnit transform has no fiscal offset.
    #[model(local)]
    pub fiscal_year_start_month: u32,
}

/// Measure axis style. Theme slot: axis_y.
///
/// Adds the measure-only fields; the x-only fields are structurally absent.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AxisYStyle {
    /// Grid line style for this axis.
    pub grid: BaseAxisGridStyle,
    /// Domain line style for this axis.
    pub line: AxisLineStyle,
    /// Tick mark style for this axis.
    pub ticks: AxisTicksStyle,
    /// Value axis label style.
    pub labels: AxisYLabelStyle,
    /// Axis title style.
    pub title: AxisTitleStyle,
    /// Per-axis scale overrides. Each axis variant owns its scale config.
    #[model(absent = "no scale override", local)]
    pub scale: Option<BaseScaleStyle>,
    /// Y-axis position; 'auto' flips when endpoint labels sit on the right.
    #[model(local)]
    pub position: Engine<YAxisPosition, Auto>,
    /// Color of the grid lines, tick labels and title; 'auto' uses the series
    /// color. Never cascaded: resolve paints it into grid, labels and title.
    #[model(absent = "the axis keeps its own colors", local)]
    pub color: Option<Engine<Color, Auto>>,
    /// Draw the y-scale on both edges (wide charts). `true` mirrors the primary
    /// axis's label verbatim; `format` or `expr` relabels only the mirrored edge
    /// while ticks stay aligned to the single shared scale. Only meaningful on axis_y.
    #[model(absent = "no mirror", local)]
    pub mirror: Option<AxisMirror>,
}

/// Scale-type overlay for quantitative axes. Theme slot: axis_quantitative.
///
/// Thin today, but a distinct type so per-slot overrides are typed precisely.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct QuantitativeAxisStyle {
    #[flatten]
    pub base: BaseAxisStyle,
}

/// Scale-type overlay for band (categorical) axes. Theme slot: axis_band.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct BandAxisStyle {
    #[flatten]
    pub base: BaseAxisStyle,
    /// Band position within the step (0 to 1).
    #[model(absent = "Vega-Lite's default", local)]
    pub band_position: Option<f64>,
}
