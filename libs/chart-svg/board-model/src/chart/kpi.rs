//! `chart/authored/kpi.py`: a headline number with an optional support line.

use super::{ChartBase, ConditionalFormattingField};
use crate::facet::{Channel, DisplayText};
use crate::string_enum;
use crate::style::kpi::KpiChartStyle;
use diagnostic::{diag, Diagnostic};
use model_core::{Authored, Spell, Tri};
use model_derive::Model;

string_enum!(
    /// Which direction of a value is good.
    Good { Up = "up", Down = "down" }
);

/// The support line beside a KPI's headline value.
#[derive(Debug, Clone, PartialEq, Model)]
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(at_least_one_of(value, label, glyph)),
    constraint(requires(value, when(set(good)))),
    constraint(at_most_one_of(good, glyph))
)]
pub struct KpiSupportConfig {
    /// Column reference (string column name) for the support number/text.
    #[model(absent = "no support value")]
    pub value: Option<Channel>,
    /// Trailing explainer text rendered beside the support value.
    #[model(absent = "no label")]
    pub label: Option<DisplayText>,
    /// Text shown before the value (e.g. '▲', '▼', '●').
    #[model(absent = "no glyph")]
    pub glyph: Option<String>,
    /// Which direction of `value` is good. The glyph and tone follow the value's sign: a change in the good direction reads positive, the opposite reads negative, and zero or null reads neutral.
    #[model(absent = "no direction is good")]
    pub good: Option<Good>,
}

/// KPI chart. It takes `label:` where the other families take `title:`, and has no subtitle.
#[derive(Debug, Clone, PartialEq, Model)]
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(at_most_one_of(good, glyph)),
    constraint(forbids("style.support.tone", when(set("support.good"))))
)]
pub struct KpiChart {
    #[flatten]
    pub formatting: ConditionalFormattingField,
    #[flatten]
    pub base: ChartBase,
    /// Column reference (string column name) for the headline number/text.
    #[model(absent = "bound by position from the query's one column")]
    pub value: Option<Channel>,
    /// Caption naming what the headline value measures; `style.variant` places it.
    #[model(absent = "no label")]
    pub label: Option<DisplayText>,
    /// Text shown before the headline value (e.g. '▲', '●').
    #[model(absent = "no glyph")]
    pub glyph: Option<String>,
    /// Which direction of `value` is good. The headline glyph and tone follow the value's sign: a change in the good direction reads positive, the opposite reads negative, and zero or null reads neutral.
    #[model(absent = "no direction is good")]
    pub good: Option<Good>,
    /// Secondary block, a delta, comparison, or note; `style.variant` places it.
    #[model(absent = "no support block")]
    pub support: Option<KpiSupportConfig>,
    /// Appearance overrides for this chart alone.
    #[model(fill = fill_style, nullable)]
    pub style: KpiChartStyle,
    /// Gradient background channel, {column, scale} shape. Paints the card background by the value's position in the scale.
    #[model(absent = "a plain card background")]
    pub background: Option<KpiBackground>,
}

/// The KPI's `background:`: a column name, or a `{column, scale}` channel object, read by the
/// style-channel parser at bind time.
#[derive(Debug, Clone, PartialEq)]
pub enum KpiBackground {
    Column(String),
    Channel(serde_json::Value),
}

impl model_core::ReadScalar for KpiBackground {
    fn read(node: &model_core::Node, cx: &mut model_core::Cx) -> Option<Self> {
        match &node.kind {
            model_core::Kind::Map(_) => Some(Self::Channel(node.to_json())),
            _ => String::read(node, cx).map(Self::Column),
        }
    }
}
impl Spell for KpiBackground {
    fn spell(&self) -> serde_json::Value {
        match self {
            Self::Column(c) => c.as_str().into(),
            Self::Channel(c) => c.clone(),
        }
    }
}

union_leaf!(
    KpiBackground,
    ::model_core::TypeExpr::Either(
        &::model_core::TypeExpr::Facet("style_channel", ::model_core::Scalar::Str),
        &::model_core::TypeExpr::Scalar(::model_core::Scalar::Object),
    )
);

impl KpiChart {
    /// `good` lowers to sign rules on its column, which cover every nonzero value: a `default:
    /// true` rule on that column would apply only at zero or null. The rule reads the chart's
    /// `conditional_formatting:` beside `good:` and `support.good:`, which no `constraint` kind
    /// does across a map's entries. A column the support shares with the headline keeps its rules
    /// on the headline.
    pub fn good_rules(chart: &Authored<KpiChart>) -> Vec<Diagnostic> {
        let rules = &chart.formatting.conditional_formatting;
        let beside = |column: &str, key: &str| -> Vec<Diagnostic> {
            let Some(entry) = rules.get(column) else {
                return Vec::new();
            };
            let catch_all = entry
                .when
                .iter()
                .flatten()
                .any(|r| matches!(r.predicate.default, Tri::Value(_)));
            if !catch_all {
                return Vec::new();
            }
            vec![diag::err_validation_field(
                "conditional_formatting",
                format!("Value error, conditional_formatting.{column} has a `default: true` rule beside `{key}`, whose sign rules cover every nonzero value, so the default rule would apply only at zero or null. Replace it with an explicit predicate such as `eq: 0` or `between`."),
            )]
        };
        let value = match &chart.value {
            Tri::Value(v) => Some(v.as_str()),
            _ => None,
        };
        let mut out = Vec::new();
        if let (Tri::Value(_), Some(value)) = (&chart.good, value) {
            out.extend(beside(value, "good"));
        }
        if let Tri::Value(support) = &chart.support {
            if let (Tri::Value(_), Tri::Value(column)) = (&support.good, &support.value) {
                if Some(column.as_str()) != value {
                    out.extend(beside(column.as_str(), "support.good"));
                }
            }
        }
        out
    }
}
