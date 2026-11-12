//! `chart/authored/custom.py`: rows in, SVG out, drawn by a Jinja template.

use super::{Columns, SharedChartFields};
use crate::facet::{facet, Facet};
use model_core::{Cx, FromNode, Kind, Node, ReadScalar, Spell};
use model_derive::Model;
use yaml::Value;

const TEMPLATE_NAME: &str = r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$";

fn template_name(v: &str) -> Result<String, String> {
    let ok = !v.is_empty()
        && v.split('/').all(|part| {
            !part.is_empty()
                && part
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
        });
    if !ok {
        return Err(format!("String should match pattern '{TEMPLATE_NAME}'"));
    }
    Ok(v.to_string())
}

facet!(
    /// A project template's name: `templates/<name>.svg.j2`, without the suffix.
    TemplateName = "template_name",
    template_name,
    model_core::Keywords::pattern(TEMPLATE_NAME)
);

/// What a declared template option holds: text, a flag, a number or a list of text.
#[derive(Debug, Clone, PartialEq)]
pub enum OptionValue {
    Null,
    Bool(bool),
    Number(serde_json::Number),
    Text(String),
    List(Vec<String>),
}

impl ReadScalar for OptionValue {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        match &node.kind {
            Kind::Scalar(Value::Null) => Some(Self::Null),
            Kind::Scalar(Value::Bool(b)) => Some(Self::Bool(*b)),
            Kind::Scalar(Value::Number(n)) => Some(Self::Number(n.clone())),
            Kind::Scalar(Value::Str(s)) => Some(Self::Text(s.clone())),
            Kind::Seq(_) => <Vec<String> as FromNode>::whole(node, cx).map(Self::List),
            _ => {
                cx.mismatch(node, "a string, number, boolean, list of strings or null");
                None
            }
        }
    }
}
impl Spell for OptionValue {
    fn spell(&self) -> serde_json::Value {
        match self {
            Self::Null => serde_json::Value::Null,
            Self::Bool(b) => (*b).into(),
            Self::Number(n) => n.clone().into(),
            Self::Text(t) => t.as_str().into(),
            Self::List(l) => l.spell(),
        }
    }
}

union_leaf!(OptionValue, {
    use ::model_core::{Scalar::*, TypeExpr::*};
    Either(
        &Scalar(Str),
        &Either(
            &Scalar(Int),
            &Either(
                &Scalar(Float),
                &Either(&Scalar(Bool), &Either(&List(&Scalar(Str)), &Unit)),
            ),
        ),
    )
});

/// Sizing for a custom chart; its look comes from the board's `style:`.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(aspect_ratio, gt = 0)))]
pub struct CustomChartStyle {
    /// Chart aspect ratio (width/height).
    #[model(absent = "sized by the theme cascade")]
    pub aspect_ratio: Option<f64>,
    /// Minimum chart height in pixels.
    #[model(absent = "no minimum")]
    pub min_height: Option<f64>,
    /// Maximum chart height in pixels.
    #[model(absent = "no maximum")]
    pub max_height: Option<f64>,
}

/// Custom chart: a project template or inline SVG source, and the columns its header declares.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(
    constraint(at_least_one_of(template, svg,
        message = "Custom chart needs `template:` (a project template name) or `svg:` (inline template source)."
    )),
    constraint(at_most_one_of(template, svg,
        message = "Custom chart takes `template:` or `svg:`, not both."
    ))
)]
pub struct CustomChart {
    #[flatten]
    pub shared: SharedChartFields,
    /// Name of a project template: `templates/<name>.svg.j2`. Exactly one of `template` or `svg` is required.
    #[model(absent = "the chart is drawn from `svg:`")]
    pub template: Option<TemplateName>,
    /// Inline Jinja SVG template source, used instead of `template`.
    #[model(absent = "the chart is drawn from `template:`")]
    pub svg: Option<String>,
    /// Column(s) for the template's `x` channel; the template header declares it.
    #[model(absent = "channel not bound")]
    pub x: Option<Columns>,
    /// Column(s) for the template's `y` channel; the template header declares it.
    #[model(absent = "channel not bound")]
    pub y: Option<Columns>,
    /// Column(s) for the template's `color` channel; the template header declares it.
    #[model(absent = "channel not bound")]
    pub color: Option<Columns>,
    /// Column(s) for the template's `size` channel; the template header declares it.
    #[model(absent = "channel not bound")]
    pub size: Option<Columns>,
    /// Column(s) for the template's `shape` channel; the template header declares it.
    #[model(absent = "channel not bound")]
    pub shape: Option<Columns>,
    /// Column(s) for the template's `theta` channel; the template header declares it.
    #[model(absent = "channel not bound")]
    pub theta: Option<Columns>,
    /// Column(s) for the template's `value` channel; the template header declares it.
    #[model(absent = "channel not bound")]
    pub value: Option<Columns>,
    /// Values for the options the template declares in its header.
    #[model(fill = fill_empty)]
    pub options: indexmap::IndexMap<String, OptionValue>,
    /// Sizing overrides for this chart alone.
    #[model(fill = fill_style, nullable)]
    pub style: CustomChartStyle,
}
