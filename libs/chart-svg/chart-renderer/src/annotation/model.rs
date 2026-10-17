//! The resolved annotation payload: `resolved.annotations[i]`, written by
//! Python resolve with every leaf set. `libs/chart-svg/fixtures/annotations/contract.json`
//! pins its shape for both readers.

use serde::Deserialize;
use serde_json::Value;

use super::Plane;
use crate::codes;
use crate::diagnostic::Diagnostic;
use crate::families::temporal::date_text_day;

/// A number, or an ISO date (`YYYY-MM-DD[THH:MM[:SS]]`). Dates are never numbers.
#[derive(Deserialize, Debug, Clone, PartialEq)]
#[serde(untagged)]
pub enum Scalar {
    Number(f64),
    Date(String),
}

impl Scalar {
    /// The date as days since the epoch, the families' own reader.
    pub fn days(&self) -> Option<f64> {
        match self {
            Scalar::Date(text) => date_text_day(text),
            Scalar::Number(_) => None,
        }
    }

    /// The scalar as written, for the aria label.
    pub fn text(&self) -> String {
        match self {
            Scalar::Number(n) if n.fract() == 0.0 && n.abs() < 1e15 => format!("{}", *n as i64),
            Scalar::Number(n) => n.to_string(),
            Scalar::Date(text) => text.clone(),
        }
    }

    /// Days for a date, the number itself otherwise: the one ordering a
    /// trend's x values are checked against.
    fn ordinal(&self) -> Option<f64> {
        match self {
            Scalar::Number(n) => Some(*n),
            Scalar::Date(_) => self.days(),
        }
    }
}

#[derive(Deserialize, Debug, Clone, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Anchor {
    Value { value: Scalar },
    /// A `None` bound extends to the domain edge.
    Range { from: Option<Scalar>, to: Option<Scalar> },
    Category { value: String },
}

impl Anchor {
    fn text(&self) -> String {
        match self {
            Anchor::Value { value } => value.text(),
            Anchor::Range { from, to } => {
                let bound = |b: &Option<Scalar>| b.as_ref().map_or("edge".to_string(), Scalar::text);
                format!("{} to {}", bound(from), bound(to))
            }
            Anchor::Category { value } => value.clone(),
        }
    }
}

#[derive(Deserialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Kind {
    Rule,
    Band,
    Note,
    Trend,
}

impl Kind {
    pub fn name(self) -> &'static str {
        match self {
            Kind::Rule => "rule",
            Kind::Band => "band",
            Kind::Note => "note",
            Kind::Trend => "trend",
        }
    }
}

/// Which y scale the y anchor reads: the base axis or a `layers:` entry's.
#[derive(Deserialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum YAxis {
    Base,
    Layer(usize),
}

#[derive(Deserialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Connector {
    None,
    Line,
    Arrow,
}

#[derive(Deserialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Enclose {
    Circle,
    Rect,
}

#[derive(Deserialize, Debug, Clone, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Trend {
    /// `(x, y)` samples, x ascending.
    pub line: Vec<(Scalar, f64)>,
    /// `(x, lo, hi)` at the same x values as `line`.
    pub interval: Option<Vec<(Scalar, f64, f64)>>,
}

#[derive(Deserialize, Debug, Clone, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Stroke {
    pub color: String,
    pub width: f64,
    pub dasharray: Option<String>,
}

#[derive(Deserialize, Debug, Clone, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct RuleStyle {
    pub opacity: f64,
    pub stroke: Stroke,
}

#[derive(Deserialize, Debug, Clone, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct RectStyle {
    pub opacity: f64,
    pub fill: String,
    pub stroke: Option<Stroke>,
    /// Enclosure inset around a note's text box, px.
    pub padding: f64,
}

#[derive(Deserialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Curve {
    Linear,
    Natural,
}

#[derive(Deserialize, Debug, Clone, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct LineStyle {
    pub opacity: f64,
    pub curve: Curve,
    pub stroke: Stroke,
}

#[derive(Deserialize, Debug, Clone, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct AreaStyle {
    pub opacity: f64,
    pub fill: String,
}

#[derive(Deserialize, Debug, Clone, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct CircleStyle {
    pub opacity: f64,
    pub radius: f64,
    pub stroke: Stroke,
}

#[derive(Deserialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum FontStyle {
    Normal,
    Italic,
}

#[derive(Deserialize, Debug, Clone, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Font {
    pub family: Vec<String>,
    pub size: f64,
    pub weight: f64,
    pub style: FontStyle,
    pub color: String,
}

#[derive(Deserialize, Debug, Clone, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BoxStroke {
    pub color: String,
    pub width: f64,
}

#[derive(Deserialize, Debug, Clone, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BoxStyle {
    pub visible: bool,
    pub fill: String,
    pub opacity: f64,
    pub padding: f64,
    pub radius: f64,
    pub stroke: Option<BoxStroke>,
}

#[derive(Deserialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Edge {
    Top,
    Bottom,
    Left,
    Right,
}

#[derive(Deserialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Align {
    Start,
    Center,
    End,
}

#[derive(Deserialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Position {
    pub edge: Edge,
    pub align: Align,
    pub overlay: bool,
}

#[derive(Deserialize, Debug, Clone, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct TextStyle {
    pub font: Font,
    /// Canvas-colored stroke width behind the text, px; 0 = none.
    pub halo: f64,
    #[serde(rename = "box")]
    pub frame: BoxStyle,
    pub position: Position,
    pub dx: f64,
    pub dy: f64,
}

#[derive(Deserialize, Debug, Clone, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Style {
    pub rule: RuleStyle,
    pub rect: RectStyle,
    pub line: LineStyle,
    pub area: AreaStyle,
    pub circle: CircleStyle,
    pub text: TextStyle,
}

#[derive(Deserialize, Debug, Clone, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Annotation {
    /// Position in the authored list, the `index` of every diagnostic.
    pub index: usize,
    #[serde(rename = "type")]
    pub kind: Kind,
    pub plane: Plane,
    /// `None` draws on every panel; else the canonical panel key.
    pub panel: Option<String>,
    /// The series a `series: each` expansion made this for; aria only.
    pub series: Option<String>,
    pub y_axis: YAxis,
    pub x: Option<Anchor>,
    pub y: Option<Anchor>,
    pub text: Option<String>,
    pub connector: Connector,
    pub enclose: Option<Enclose>,
    pub trend: Option<Trend>,
    pub style: Style,
}

impl Annotation {
    /// `ERR-ANNOTATION-PAYLOAD` for this annotation.
    pub fn payload_error(&self, chart_id: &str, reason: impl Into<String>) -> Diagnostic {
        payload_error(chart_id, self.index, reason)
    }

    /// Re-checks the per-type anchor table resolve already enforced, so a
    /// resolve bug is refused here instead of drawn.
    pub fn validate(&self, chart_id: &str) -> Result<(), Diagnostic> {
        let fail = |reason: &str| Err(self.payload_error(chart_id, reason));
        let is_value = |a: &Option<Anchor>| matches!(a, Some(Anchor::Value { .. }));
        let is_range = |a: &Option<Anchor>| matches!(a, Some(Anchor::Range { .. }));
        if self.text.as_deref().is_some_and(|t| t.contains('\n')) {
            return fail("text is single-line; it holds a line break");
        }
        if self.kind != Kind::Note && (self.connector != Connector::None || self.enclose.is_some()) {
            return fail("connector and enclose are note-only");
        }
        if self.kind != Kind::Trend && self.trend.is_some() {
            return fail("trend geometry on a non-trend annotation");
        }
        match self.kind {
            Kind::Rule => {
                let ok = (is_value(&self.y) && (self.x.is_none() || is_range(&self.x)))
                    || (is_value(&self.x) && (self.y.is_none() || is_range(&self.y)));
                if !ok {
                    return fail("a rule takes one value anchor, the other axis a range or nothing");
                }
            }
            Kind::Band => {
                if matches!(self.y, Some(Anchor::Category { .. })) {
                    return fail("a band's y anchor is a value or a range, never a category");
                }
                if self.x.is_none() && self.y.is_none() {
                    return fail("a band needs an x or a y anchor");
                }
            }
            Kind::Note => {
                let anchored = match (&self.x, &self.y) {
                    (None, None) => false,
                    (x, y) if is_value(x) && is_value(y) => true,
                    _ => return fail("a note takes value anchors on both x and y, or neither"),
                };
                if !anchored && (self.connector != Connector::None || self.enclose.is_some()) {
                    return fail("a frame note takes no connector or enclose");
                }
            }
            Kind::Trend => {
                if self.x.is_some() || self.y.is_some() {
                    return fail("a trend's geometry is its samples, not anchors");
                }
                let Some(trend) = &self.trend else { return fail("a trend needs samples") };
                validate_trend(trend).map_err(|reason| self.payload_error(chart_id, reason))?;
            }
        }
        Ok(())
    }

    /// The accessible name: the text, else the type and its anchors.
    pub fn label(&self) -> String {
        if let Some(text) = &self.text {
            return text.clone();
        }
        let anchors: Vec<String> = [&self.x, &self.y].into_iter().flatten().map(Anchor::text).collect();
        if anchors.is_empty() {
            self.kind.name().to_string()
        } else {
            format!("{} at {}", self.kind.name(), anchors.join(", "))
        }
    }
}

fn validate_trend(trend: &Trend) -> Result<(), String> {
    if trend.line.len() < 2 {
        return Err("a trend needs at least two samples".into());
    }
    let xs = trend
        .line
        .iter()
        .map(|(x, _)| x.ordinal().ok_or_else(|| format!("trend x {:?} is not a date", x.text())))
        .collect::<Result<Vec<f64>, String>>()?;
    let numbers = trend.line.iter().filter(|(x, _)| matches!(x, Scalar::Number(_))).count();
    if numbers != 0 && numbers != xs.len() {
        return Err("trend x values mix numbers and dates".into());
    }
    if xs.windows(2).any(|w| w[0] >= w[1]) {
        return Err("trend x values are not strictly ascending".into());
    }
    if let Some(interval) = &trend.interval {
        let same_x = interval.len() == trend.line.len()
            && interval.iter().zip(&trend.line).all(|((xi, _, _), (xl, _))| xi == xl);
        if !same_x {
            return Err("the trend interval's x values differ from its line's".into());
        }
    }
    Ok(())
}

pub fn payload_error(chart_id: &str, index: usize, reason: impl Into<String>) -> Diagnostic {
    Diagnostic::new(
        &codes::ERR_ANNOTATION_PAYLOAD,
        [
            ("chart_id", Value::String(chart_id.to_string())),
            ("index", Value::from(index)),
            ("reason", Value::String(reason.into())),
        ],
    )
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    const CONTRACT: &str = include_str!("../../../fixtures/annotations/contract.json");

    pub(crate) fn contract() -> Vec<Annotation> {
        serde_json::from_str(CONTRACT).expect("the contract fixture deserializes")
    }

    fn contract_json() -> Vec<Value> {
        serde_json::from_str(CONTRACT).unwrap()
    }

    fn parse(v: Value) -> Result<Annotation, serde_json::Error> {
        serde_json::from_value(v)
    }

    #[test]
    fn the_contract_fixture_deserializes_and_validates() {
        let all = contract();
        assert_eq!(all.len(), 13);
        for a in &all {
            a.validate("c").unwrap_or_else(|d| panic!("annotation {}: {d}", a.index));
        }
        assert!(all.iter().any(|a| a.kind == Kind::Trend && a.y_axis == YAxis::Layer(0)));
        assert_eq!(all[2].x, Some(Anchor::Range { from: Some(Scalar::Date("2024-10-01".into())), to: None }));
        assert_eq!(all[0].y, Some(Anchor::Value { value: Scalar::Number(25_000_000.0) }));
    }

    /// One unknown key per `deny_unknown_fields` struct, each refused.
    #[test]
    fn every_struct_refuses_an_unknown_key() {
        let paths: &[&[&str]] = &[
            &[],
            &["style"],
            &["style", "rule"],
            &["style", "rule", "stroke"],
            &["style", "rect"],
            &["style", "line"],
            &["style", "area"],
            &["style", "circle"],
            &["style", "text"],
            &["style", "text", "font"],
            &["style", "text", "box"],
            &["style", "text", "position"],
            &["y"],
            &["trend"],
        ];
        for path in paths {
            let mut v = contract_json()[if path == &["trend"] { 11 } else { 0 }].clone();
            let mut node = &mut v;
            for key in *path {
                node = &mut node[*key];
            }
            node.as_object_mut().expect("an object").insert("bogus".into(), Value::Bool(true));
            assert!(parse(v).is_err(), "{path:?} accepted an unknown key");
        }
        let mut v = contract_json()[9].clone();
        v["style"]["text"]["box"]["stroke"]["bogus"] = Value::Bool(true);
        assert!(parse(v).is_err(), "box stroke accepted an unknown key");
    }

    fn with(i: usize, edit: impl FnOnce(&mut Value)) -> Annotation {
        let mut v = contract_json()[i].clone();
        edit(&mut v);
        parse(v).expect("still well-formed")
    }

    fn refused(a: Annotation) -> String {
        let d = a.validate("c").expect_err("validate refuses it");
        assert_eq!(d.code, "ERR-ANNOTATION-PAYLOAD");
        assert_eq!(d.params["chart_id"], "c");
        assert_eq!(d.params["index"], a.index);
        d.params["reason"].as_str().unwrap().to_string()
    }

    #[test]
    fn validate_rejects_each_anchor_shape_violation() {
        fn value() -> Value {
            serde_json::json!({"kind": "value", "value": 1})
        }
        fn range() -> Value {
            serde_json::json!({"kind": "range", "from": 1, "to": 2})
        }
        fn category() -> Value {
            serde_json::json!({"kind": "category", "value": "East"})
        }
        let cases: Vec<(usize, Box<dyn FnOnce(&mut Value)>)> = vec![
            // rule: both value
            (0, Box::new(|v: &mut Value| v["x"] = value())),
            // rule: neither value
            (0, Box::new(|v: &mut Value| v["y"] = range())),
            // rule: category
            (0, Box::new(|v: &mut Value| v["x"] = category())),
            // rule: no anchors
            (0, Box::new(|v: &mut Value| v["y"] = Value::Null)),
            // band: category y
            (4, Box::new(|v: &mut Value| v["y"] = category())),
            // band: no anchors
            (4, Box::new(|v: &mut Value| v["y"] = Value::Null)),
            // note: one anchor
            (8, Box::new(|v: &mut Value| v["y"] = Value::Null)),
            // note: a range
            (8, Box::new(|v: &mut Value| v["y"] = range())),
            // frame note: connector
            (10, Box::new(|v: &mut Value| v["connector"] = "arrow".into())),
            // frame note: enclose
            (10, Box::new(|v: &mut Value| v["enclose"] = "circle".into())),
            // connector off a note
            (0, Box::new(|v: &mut Value| v["connector"] = "line".into())),
            // trend: anchors
            (12, Box::new(|v: &mut Value| v["x"] = value())),
            // trend: no samples
            (12, Box::new(|v: &mut Value| v["trend"] = Value::Null)),
            // trend geometry on a rule
            (0, Box::new(|v: &mut Value| v["trend"] = serde_json::json!({"line": [[0, 1], [1, 2]], "interval": null}))),
            // trend: one sample
            (12, Box::new(|v: &mut Value| v["trend"]["line"] = serde_json::json!([[0, 1]]))),
            // trend: descending
            (12, Box::new(|v: &mut Value| v["trend"]["line"] = serde_json::json!([[2, 1], [1, 2]]))),
            // trend: mixed
            (12, Box::new(|v: &mut Value| v["trend"]["line"] = serde_json::json!([[2, 1], ["2024-01-01", 2]]))),
            // trend: interval x differ
            (11, Box::new(|v: &mut Value| v["trend"]["interval"][1][0] = "2024-02-02".into())),
            // text with a line break
            (0, Box::new(|v: &mut Value| v["text"] = "two\nlines".into())),
        ];
        for (n, (i, edit)) in cases.into_iter().enumerate() {
            let reason = refused(with(i, edit));
            assert!(!reason.is_empty(), "case {n}");
        }
    }

    #[test]
    fn the_label_is_the_text_else_the_type_and_raw_anchors() {
        let all = contract();
        assert_eq!(all[0].label(), "FY24 goal");
        assert_eq!(all[5].label(), "band at East");
        assert_eq!(all[12].label(), "trend");
        let rule = with(2, |v| v["text"] = Value::Null);
        assert_eq!(rule.label(), "rule at 2024-10-01 to edge, 21000000.5");
    }
}
