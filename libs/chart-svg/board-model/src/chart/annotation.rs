//! `chart/authored/_annotation.py` and `_annotations.py`: rules, bands, notes and trends drawn over
//! the plot, the chart `sort:` and the donut `total:`.
//!
//! An anchor is a literal in the axis column's type, a range, or a call from a closed function set
//! (`avg()`, `percentile(25)`, `now()`, `current(month)`). A call string is read as the mapping
//! Python's before-validator turns it into (`{fn: percentile, percent: 25}`), so the typed model
//! checks it once whichever way it was written. What a literal means (a date, a number, a
//! category) depends on the axis it lands on, which `bind` knows.
//!
//! Dissolved into parse: `NoteAnchor`'s range check (a note takes a scalar anchor, a type error).
//! A note's x/y pairing and `enclose` / `connector` / `max_width` rules are `constraint`s; the
//! pairing is also checked on an authored note by [`NoteAnnotation::pairing`].

use crate::facet::{facet, Facet};
use crate::string_enum;
use crate::style::annotations::{
    BandAnnotationStyle, NoteAnnotationStyle, RuleAnnotationStyle, TrendAnnotationStyle,
};
use diagnostic::diag;
use diagnostic::Diagnostic;
use model_core::{
    satisfied, Authored, Cx, Doc, Entry, FromNode, Kind, Leaf, MapRead, Model, Node, Patch,
    ReadScalar, ScalarValue as Value, Spell, Tri,
};
use model_derive::Model;
use regex::Regex;
use std::sync::OnceLock;

string_enum!(
    /// Which aggregate an anchor computes over the plotted rows.
    AggregateFn { Min = "min", Max = "max", Avg = "avg", Median = "median", Percentile = "percentile" }
);
string_enum!(
    /// Which clock reading an anchor takes.
    ClockFn { Now = "now", Current = "current" }
);
string_enum!(
    /// The calendar period a `current()` call fills.
    ClockUnit { Day = "day", Week = "week", Month = "month", Quarter = "quarter", Year = "year" }
);
string_enum!(
    /// Shape of a fitted curve.
    FitKind {
        Linear = "linear",
        Polynomial = "polynomial",
        Exponential = "exponential",
        Log = "log",
        Power = "power",
    }
);
string_enum!(
    /// Interval of the fitted mean, or of a new observation.
    IntervalKind { Confidence = "confidence", Prediction = "prediction" }
);
string_enum!(
    /// Draw below the data marks, or above them.
    AnnotationPlane { Background = "background", Foreground = "foreground" }
);
string_enum!(
    /// A line, or a line ending in an arrowhead, from the anchor to text moved away with dx/dy.
    NoteConnector { Line = "line", Arrow = "arrow" }
);
string_enum!(
    /// Shape drawn around a note's anchor.
    NoteEnclose { Circle = "circle", Rect = "rect" }
);
string_enum!(
    /// Sort direction.
    SortOrder { Asc = "asc", Desc = "desc" }
);

/// A value computed over the plotted rows of the axis column.
#[derive(Debug, Clone, PartialEq, Model)]
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(requires(percent, when(is_in("fn", "percentile")))),
    constraint(forbids(percent, when(not_in("fn", "percentile")))),
    constraint(range(percent, ge = 0, le = 100))
)]
pub struct AggregateAnchor {
    /// Which aggregate.
    pub r#fn: AggregateFn,
    /// The percentile, 0 to 100.
    #[model(absent = "no percent: only percentile takes one")]
    pub percent: Option<f64>,
}

/// The render time, or the calendar period that holds it.
#[derive(Debug, Clone, PartialEq, Model)]
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(requires(unit, when(is_in("fn", "current")))),
    constraint(forbids(unit, when(is_in("fn", "now"))))
)]
pub struct ClockAnchor {
    /// Which clock reading.
    pub r#fn: ClockFn,
    /// The period a current() call fills.
    #[model(absent = "no unit: now() takes none")]
    pub unit: Option<ClockUnit>,
}

/// The function a trend fits to the plotted rows.
#[derive(Debug, Clone, PartialEq, Model)]
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(requires(order, when(is_in(kind, "polynomial")),
        message = "A polynomial fit takes an order."
    )),
    constraint(forbids(order, when(not_in(kind, "polynomial")),
        message = "Only a polynomial fit takes an order."
    )),
    constraint(range(order, ge = 2))
)]
pub struct FitFunction {
    /// Shape of the fitted curve.
    pub kind: FitKind,
    /// Degree of a polynomial fit.
    #[model(absent = "no order: only a polynomial fit takes one")]
    pub order: Option<i64>,
}

/// The band drawn around a trend.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(percent, gt = 0, lt = 100)))]
pub struct IntervalFunction {
    /// Interval of the fitted mean, or of a new observation.
    pub kind: IntervalKind,
    /// Coverage, in percent.
    pub percent: f64,
}

fn call_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    #[allow(clippy::expect_used)]
    RE.get_or_init(|| Regex::new(r"^\s*([A-Za-z_]+)\s*\((.*)\)\s*$").expect("a valid pattern"))
}

/// `name(arg)` as `(name, arg)`.
fn call(text: &str) -> Option<(&str, &str)> {
    let c = call_re().captures(text)?;
    Some((c.get(1)?.as_str(), c.get(2)?.as_str().trim()))
}

fn repr(s: &str) -> String {
    diagnostic::py_repr(&serde_json::Value::String(s.to_string()))
}

fn number(arg: &str, what: &str) -> Result<f64, String> {
    arg.trim()
        .parse::<f64>()
        .map_err(|_| format!("{what} takes a number, got {}", repr(arg)))
}

fn num(v: f64) -> Result<Value, String> {
    serde_json::Number::from_f64(v)
        .map(Value::Number)
        .ok_or_else(|| format!("{v} is not a finite number"))
}

/// A call's mapping form, spanned at the string it came from.
fn mapping(node: &Node, fields: Vec<(&str, Value)>) -> Node {
    let map = fields
        .into_iter()
        .map(|(k, v)| {
            let value = Node {
                span: node.span,
                kind: Kind::Scalar(v),
            };
            (k.to_string(), Entry::string_key(node.span, k, value))
        })
        .collect();
    Node {
        span: node.span,
        kind: Kind::Map(map),
    }
}

const ANCHOR_FUNCTIONS: &str = "min(), max(), avg(), median(), percentile(<0-100>), now(), current(day | week | month | quarter | year)";

/// `Ok(None)` is not a call: the string is a literal.
fn anchor_call(text: &str) -> Result<Option<Vec<(&'static str, Value)>>, String> {
    let Some((name, arg)) = call(text) else {
        return Ok(None);
    };
    let name_value = |n: &str| Value::Str(n.to_string());
    Ok(Some(match name {
        "min" | "max" | "avg" | "median" | "now" if arg.is_empty() => {
            vec![("fn", name_value(name))]
        }
        "percentile" if !arg.is_empty() => {
            let percent = number(arg, "percentile()")?;
            if !(0.0..=100.0).contains(&percent) {
                return Err(format!("function percentile() takes 0 to 100, got {arg}"));
            }
            vec![("fn", name_value(name)), ("percent", num(percent)?)]
        }
        "current" if ClockUnit::VALUES.contains(&arg) => {
            vec![("fn", name_value(name)), ("unit", name_value(arg))]
        }
        _ => {
            return Err(format!(
                "function {} is not an anchor function; anchors take {ANCHOR_FUNCTIONS}, with no nesting or arithmetic",
                repr(text.trim())
            ))
        }
    }))
}

/// A fit written as a call, `linear()` or `polynomial(2)`.
fn fit_call(text: &str) -> Result<Vec<(&'static str, Value)>, String> {
    let bad = || {
        format!(
            "fit {} is not a fit function; use linear(), polynomial(<order 2 or more>), exponential(), log() or power()",
            repr(text)
        )
    };
    let (name, arg) = call(text).ok_or_else(bad)?;
    let kind = Value::Str(name.to_string());
    match name {
        "linear" | "exponential" | "log" | "power" if arg.is_empty() => Ok(vec![("kind", kind)]),
        "polynomial" if !arg.is_empty() && arg.chars().all(|c| c.is_ascii_digit()) => {
            match arg.parse::<i64>() {
                Ok(order) if order >= 2 => {
                    Ok(vec![("kind", kind), ("order", Value::Number(order.into()))])
                }
                _ => Err(bad()),
            }
        }
        _ => Err(bad()),
    }
}

/// An interval written as a call, `confidence(95)`.
fn interval_call(text: &str) -> Result<Vec<(&'static str, Value)>, String> {
    let bad = || {
        format!(
            "interval {} is not an interval function; use confidence(<pct>) or prediction(<pct>), with pct between 0 and 100",
            repr(text)
        )
    };
    let (name, arg) = call(text).ok_or_else(bad)?;
    if !matches!(name, "confidence" | "prediction") || arg.is_empty() {
        return Err(bad());
    }
    let percent = number(arg, &format!("{name}()"))?;
    if !(percent > 0.0 && percent < 100.0) {
        return Err(bad());
    }
    Ok(vec![
        ("kind", Value::Str(name.to_string())),
        ("percent", num(percent)?),
    ])
}

/// A string read through its call form, else the node as it stands.
fn read_called<T: FromNode>(
    node: &Node,
    cx: &mut Cx,
    parse: impl FnOnce(&str) -> Result<Option<Vec<(&'static str, Value)>>, String>,
) -> Option<T::Whole> {
    if let Kind::Scalar(Value::Str(s)) = &node.kind {
        return match parse(s) {
            Ok(Some(fields)) => T::whole(&mapping(node, fields), cx),
            Ok(None) => None,
            Err(msg) => {
                cx.invalid(node.span, format!("Value error, {msg}"));
                None
            }
        };
    }
    T::whole(node, cx)
}

/// A scalar anchor: a number, a date, a category text, or a computed one.
#[derive(Debug, Clone, PartialEq)]
pub enum AnchorScalar {
    Number(f64),
    /// A YAML date or timestamp, as the ISO text a board dumps.
    Date(String),
    Text(String),
    Aggregate(<AggregateAnchor as Leaf>::Whole),
    Clock(<ClockAnchor as Leaf>::Whole),
}

impl ReadScalar for AnchorScalar {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        match &node.kind {
            Kind::Scalar(Value::Number(n)) => n.as_f64().map(Self::Number),
            Kind::Scalar(Value::Timestamp(s)) => Some(Self::Date(s.clone())),
            Kind::Scalar(Value::Str(s)) => match anchor_call(s) {
                Ok(None) => Some(Self::Text(s.clone())),
                Ok(Some(fields)) => {
                    let named = fields.first().map(|f| &f.1);
                    let as_clock =
                        matches!(named, Some(Value::Str(n)) if n == "now" || n == "current");
                    let mapped = mapping(node, fields);
                    if as_clock {
                        <ClockAnchor as FromNode>::whole(&mapped, cx).map(Self::Clock)
                    } else {
                        <AggregateAnchor as FromNode>::whole(&mapped, cx).map(Self::Aggregate)
                    }
                }
                Err(msg) => {
                    cx.invalid(node.span, format!("Value error, {msg}"));
                    None
                }
            },
            Kind::Map(m) => {
                let clock = matches!(m.get("fn").map(|e| &e.value.kind),
                    Some(Kind::Scalar(Value::Str(n))) if n == "now" || n == "current");
                let read = cx.attempt(|cx| {
                    if clock {
                        <ClockAnchor as FromNode>::whole(node, cx).map(Self::Clock)
                    } else {
                        <AggregateAnchor as FromNode>::whole(node, cx).map(Self::Aggregate)
                    }
                });
                let rules = match clock {
                    true => ClockAnchor::info().constraints,
                    false => AggregateAnchor::info().constraints,
                };
                let json = node.to_json();
                let mut holds = true;
                for c in rules {
                    match satisfied(c, &Doc(&json)) {
                        Ok(ok) => holds &= ok,
                        Err(e) => {
                            cx.invalid(node.span, format!("an anchor rule cannot be read: {}", e.0));
                            return None;
                        }
                    }
                }
                if let (Ok(Some(anchor)), true) = (read, holds) {
                    return Some(anchor);
                }
                // Python reads a mapping anchor as one of its arms and, when none fits, reports
                // every key of it as extra under the range arm; the keys are the same here. The
                // inner read's own errors are dropped on purpose: Python never shows them.
                if let Some(m) = MapRead::new(node) {
                    cx.unknown_keys(&m);
                }
                None
            }
            _ => {
                cx.mismatch(node, "a number, a date, a string or an anchor function");
                None
            }
        }
    }
}
impl Spell for AnchorScalar {
    fn spell(&self) -> ::serde_json::Value {
        match self {
            Self::Number(n) => n.spell(),
            Self::Date(s) | Self::Text(s) => s.spell(),
            Self::Aggregate(a) => a.spell(),
            Self::Clock(c) => c.spell(),
        }
    }
}

union_leaf!(
    AnchorScalar,
    ::model_core::TypeExpr::Either(
        &::model_core::TypeExpr::Scalar(::model_core::Scalar::Float),
        &::model_core::TypeExpr::Either(
            &::model_core::TypeExpr::Scalar(::model_core::Scalar::Int),
            &::model_core::TypeExpr::Scalar(::model_core::Scalar::Str),
        ),
    )
);

/// A span on one axis; a missing bound runs to the axis edge.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(at_least_one_of(from, to,
        message = "A range needs from or to."
    )))]
pub struct AnchorRange {
    /// Where the span starts.
    #[model(absent = "the span runs from the axis edge")]
    pub from: Option<AnchorScalar>,
    /// Where the span ends.
    #[model(absent = "the span runs to the axis edge")]
    pub to: Option<AnchorScalar>,
}

/// Where an annotation sits on an axis: a scalar anchor or a range.
#[derive(Debug, Clone, PartialEq)]
pub enum Anchor {
    Scalar(AnchorScalar),
    Range(<AnchorRange as Leaf>::Whole),
}

fn is_range(node: &Node) -> bool {
    matches!(&node.kind, Kind::Map(m) if m.contains_key("from") || m.contains_key("to"))
}

impl ReadScalar for Anchor {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        if is_range(node) {
            return <AnchorRange as FromNode>::whole(node, cx).map(Self::Range);
        }
        AnchorScalar::read(node, cx).map(Self::Scalar)
    }
}
impl Spell for Anchor {
    fn spell(&self) -> ::serde_json::Value {
        match self {
            Self::Scalar(s) => s.spell(),
            Self::Range(r) => r.spell(),
        }
    }
}

union_leaf!(
    Anchor,
    ::model_core::TypeExpr::Either(
        &::model_core::TypeExpr::Ref("AnchorRange", <AnchorRange as ::model_core::Model>::info),
        &<AnchorScalar as Leaf>::EXPR,
    )
);

/// A note's anchor: one value, never a range.
#[derive(Debug, Clone, PartialEq)]
pub struct NoteAnchor(pub AnchorScalar);

impl ReadScalar for NoteAnchor {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        if is_range(node) {
            cx.invalid(
                node.span,
                "Value error, a note takes a single value, not a range",
            );
            return None;
        }
        AnchorScalar::read(node, cx).map(Self)
    }
}
impl Spell for NoteAnchor {
    fn spell(&self) -> ::serde_json::Value {
        self.0.spell()
    }
}

union_leaf!(NoteAnchor, <AnchorScalar as Leaf>::EXPR);

/// The fitted function of a trend, `linear()` or `{kind: polynomial, order: 2}`.
#[derive(Debug, Clone, PartialEq)]
pub struct Fit(pub <FitFunction as Leaf>::Whole);

impl ReadScalar for Fit {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        let called = |s: &str| fit_call(s).map(Some);
        read_called::<FitFunction>(node, cx, called).map(Self)
    }
}
impl Spell for Fit {
    fn spell(&self) -> ::serde_json::Value {
        self.0.spell()
    }
}

union_leaf!(
    Fit,
    ::model_core::TypeExpr::Either(
        &::model_core::TypeExpr::Ref("FitFunction", <FitFunction as ::model_core::Model>::info),
        &::model_core::TypeExpr::Facet("fit_call", ::model_core::Scalar::Str),
    )
);

/// The band around a trend, `confidence(95)` or `{kind: confidence, percent: 95}`.
#[derive(Debug, Clone, PartialEq)]
pub struct Interval(pub <IntervalFunction as Leaf>::Whole);

impl ReadScalar for Interval {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        let called = |s: &str| interval_call(s).map(Some);
        read_called::<IntervalFunction>(node, cx, called).map(Self)
    }
}
impl Spell for Interval {
    fn spell(&self) -> ::serde_json::Value {
        self.0.spell()
    }
}

union_leaf!(
    Interval,
    ::model_core::TypeExpr::Either(
        &::model_core::TypeExpr::Ref(
            "IntervalFunction",
            <IntervalFunction as ::model_core::Model>::info
        ),
        &::model_core::TypeExpr::Facet("interval_call", ::model_core::Scalar::Str),
    )
);

fn annotation_text(v: &str) -> Result<String, String> {
    static RE: OnceLock<Regex> = OnceLock::new();
    #[allow(clippy::expect_used)]
    let re = RE.get_or_init(|| Regex::new(r"^[^\n\r]*\S[^\n\r]*$").expect("a valid pattern"));
    if re.is_match(v) {
        return Ok(v.to_string());
    }
    Err(if v.is_empty() {
        "String should have at least 1 character".to_string()
    } else {
        r"String should match pattern '^[^\n\r]*\S[^\n\r]*$'".to_string()
    })
}

facet!(
    /// Text drawn with an annotation: one non-blank line.
    AnnotationText = "annotation_text",
    annotation_text,
    model_core::Keywords::pattern(r"^[^\n\r]*\S[^\n\r]*$")
        .in_arm()
        .at_least_chars(1)
);

fn non_empty(v: &str) -> Result<String, String> {
    if v.is_empty() {
        return Err("String should have at least 1 character".to_string());
    }
    Ok(v.to_string())
}

facet!(
    /// A non-empty column name.
    ColumnName = "non_empty",
    non_empty,
    model_core::Keywords::NONE.at_least_chars(1)
);

/// Which series a computed anchor or trend is computed for.
#[derive(Debug, Clone, PartialEq)]
pub enum SeriesSelection {
    Each,
    Total,
    Names(Vec<String>),
}

impl ReadScalar for SeriesSelection {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        match &node.kind {
            Kind::Scalar(Value::Str(s)) if s == "each" => Some(Self::Each),
            Kind::Scalar(Value::Str(s)) if s == "total" => Some(Self::Total),
            Kind::Seq(_) => <Vec<String> as FromNode>::whole(node, cx).map(Self::Names),
            _ => {
                cx.mismatch(node, "'each', 'total' or a list of series names");
                None
            }
        }
    }
}
impl Spell for SeriesSelection {
    fn spell(&self) -> ::serde_json::Value {
        match self {
            Self::Each => "each".into(),
            Self::Total => "total".into(),
            Self::Names(n) => n.spell(),
        }
    }
}

union_leaf!(
    SeriesSelection,
    ::model_core::TypeExpr::Either(
        &::model_core::TypeExpr::OneOf(&["each", "total"]),
        &::model_core::TypeExpr::List(&::model_core::TypeExpr::Scalar(::model_core::Scalar::Str)),
    )
);

/// Fields every annotation kind takes.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AnnotationBase {
    /// Text drawn with the annotation; `{{ value | format }}` prints the anchor.
    #[model(absent = "no text")]
    pub text: Option<AnnotationText>,
    /// Draw below the data marks, or above them.
    #[model(absent = "the annotation kind's own plane")]
    pub plane: Option<AnnotationPlane>,
    /// Which series a computed anchor or trend is computed for. `total` pools every series' rows, or takes the per-x sum on a stacked chart.
    #[model(absent = "each series, or the chart's only one")]
    pub series: Option<SeriesSelection>,
    /// The y column the annotation refers to: it picks the y axis that plots the column, and the rows a computed y anchor or trend reads. Required for a computed y anchor, trend or derived note on a chart with several y columns or `layers:`.
    #[model(absent = "the chart's only y column")]
    pub y_column: Option<ColumnName>,
}

/// A line across the plot at one value, or a segment of it.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(at_least_one_of(x, y,
        message = "A {type} needs an x or y anchor."
    )))]
pub struct RuleAnnotation {
    #[flatten]
    pub base: AnnotationBase,
    /// Where on the x axis.
    #[model(absent = "no x anchor")]
    pub x: Option<Anchor>,
    /// Where on the y axis.
    #[model(absent = "no y anchor")]
    pub y: Option<Anchor>,
    /// Appearance overrides for this annotation's marks.
    #[model(absent = "the theme's rule annotation style", local)]
    pub style: Option<Patch<RuleAnnotationStyle>>,
}

/// A shaded region over a range or a category.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(at_least_one_of(x, y,
        message = "A {type} needs an x or y anchor."
    )))]
pub struct BandAnnotation {
    #[flatten]
    pub base: AnnotationBase,
    /// Span or category on the x axis.
    #[model(absent = "no x anchor")]
    pub x: Option<Anchor>,
    /// Span on the y axis.
    #[model(absent = "no y anchor")]
    pub y: Option<Anchor>,
    /// Appearance overrides for this annotation's marks.
    #[model(absent = "the theme's band annotation style", local)]
    pub style: Option<Patch<BandAnnotationStyle>>,
}

/// Text at a point, or pinned to the plot frame when it has no anchor.
///
/// With `min()` or `max()` on one axis and the other omitted, the note sits at the row holding
/// that extreme.
#[derive(Debug, Clone, PartialEq, Model)]
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(requires(y, when(set(x), not_call(x, "min", "max")),
        message = "A note takes both x and y, or neither; only min() and max() can derive the other coordinate."
    )),
    constraint(requires(x, when(set(y), not_call(y, "min", "max")),
        message = "A note takes both x and y, or neither; only min() and max() can derive the other coordinate."
    )),
    constraint(forbids(enclose, when(is_in(x, null), is_in(y, null)),
        message = "A note with no anchor takes no enclose."
    )),
    constraint(forbids(
        connector,
        when(is_in(x, null), is_in(y, null), not_in(connector, null)),
        message = "A note with no anchor takes no connector."
    )),
    constraint(forbids(
        "style.marks.text.position",
        when(is_in(enclose, "rect"), is_in(connector, null)),
        message = "A note boxed with enclose: rect is centered on its anchor, so style.marks.text.position does not apply; add a connector to place the box."
    )),
    constraint(forbids("style.marks.text.max_width", when(set(x)),
        message = "style.marks.text.max_width wraps only a note with no anchor or a trend; a note anchored at a point does not wrap."
    )),
    constraint(forbids("style.marks.text.max_width", when(set(y)),
        message = "style.marks.text.max_width wraps only a note with no anchor or a trend; a note anchored at a point does not wrap."
    ))
)]
pub struct NoteAnnotation {
    #[flatten]
    pub base: AnnotationBase,
    /// Where on the x axis. `min()` or `max()` with y omitted anchors at the row holding that x extreme.
    #[model(absent = "no x anchor")]
    pub x: Option<NoteAnchor>,
    /// Where on the y axis. `min()` or `max()` with x omitted anchors at the row holding that y extreme.
    #[model(absent = "no y anchor")]
    pub y: Option<NoteAnchor>,
    /// Draws a line, or a line ending in an arrowhead, from the anchor to text moved away with dx/dy.
    #[model(absent = "no connector")]
    pub connector: Option<NoteConnector>,
    /// Shape drawn around the anchor.
    #[model(absent = "no shape around the anchor")]
    pub enclose: Option<NoteEnclose>,
    /// Appearance overrides for this annotation's marks.
    #[model(absent = "the theme's note annotation style", local)]
    pub style: Option<Patch<NoteAnnotationStyle>>,
}

/// A curve fitted to the plotted rows, with an optional interval.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TrendAnnotation {
    #[flatten]
    pub base: AnnotationBase,
    /// The fitted function, such as linear() or polynomial(2).
    pub fit: Fit,
    /// Band around the trend, such as confidence(95).
    #[model(absent = "no interval band")]
    pub interval: Option<Interval>,
    /// Span of x the trend is drawn over; defaults to the data.
    #[model(absent = "the trend spans the data")]
    pub x: Option<AnchorRange>,
    /// Appearance overrides for this annotation's marks.
    #[model(absent = "the theme's trend annotation style", local)]
    pub style: Option<Patch<TrendAnnotationStyle>>,
}

/// One chart annotation, by `type:`.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(tag = "type")]
pub enum Annotation {
    Rule(RuleAnnotation),
    Band(BandAnnotation),
    Note(NoteAnnotation),
    Trend(TrendAnnotation),
}

/// Chart-level sort configuration for categorical axes.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ChartSort {
    /// Column name to sort by. A category holding several rows (a color series, or a y: [...] list) is folded to one value of this column first, and on a bar that fold is the stacked total only when the chart stacks and this names its single y column; everything else, a y: [...] measure included, ranks by the smallest value the column holds in that category. Name a column that is constant within a category, or pre-aggregate in the query.
    pub by: String,
    /// Sort direction (asc or desc).
    #[model(fill = "asc")]
    pub order: SortOrder,
}

/// Donut center total: auto-rendered sum at the center of a donut, with author override.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ChartTotal {
    /// Whether to render the donut center total. Defaults True; set False to suppress the auto-rendered center on donuts whose theta values aren't a meaningful sum (e.g. pre-aggregated percentage shares).
    #[model(fill = true)]
    pub visible: bool,
    /// Caption text displayed below the center total value.
    #[model(absent = "no caption")]
    pub label: Option<crate::facet::DisplayText>,
}

impl NoteAnnotation {
    /// A note takes both x and y, or neither; only `min()` and `max()` can derive the other
    /// coordinate. The schema states the rule as a `constraint`; this reads it off the authored
    /// note, whose anchors are typed.
    pub fn pairing(note: &Authored<NoteAnnotation>) -> Vec<Diagnostic> {
        let extreme = |a: &Tri<NoteAnchor>| {
            matches!(a, Tri::Value(NoteAnchor(AnchorScalar::Aggregate(g)))
                if matches!(g.r#fn, Some(AggregateFn::Min | AggregateFn::Max)))
        };
        let set = |a: &Tri<NoteAnchor>| matches!(a, Tri::Value(_));
        let message = "Value error, A note takes both x and y, or neither; only min() and max() can derive the other coordinate.";
        let mut out = Vec::new();
        if set(&note.x) && !set(&note.y) && !extreme(&note.x) {
            out.push(diag::err_validation_field("y", message));
        }
        if set(&note.y) && !set(&note.x) && !extreme(&note.y) {
            out.push(diag::err_validation_field("x", message));
        }
        out
    }
}
