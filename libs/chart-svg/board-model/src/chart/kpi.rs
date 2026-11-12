//! `chart/authored/kpi.py`: a headline number with an optional support line.

use super::ChartBase;
use crate::facet::{Channel, Color, DisplayText};
use crate::string_enum;
use crate::style::kpi::KpiChartStyle;
use crate::primitives::ScaleTargetConfig;
use model_core::{Cx, FromNode, Leaf, Node, ReadScalar, Spell};
use model_derive::Model;

string_enum!(
    /// Which direction of a value is good.
    Good { Up = "up", Down = "down" }
);

/// The support line beside a KPI's headline value.
#[derive(Debug, Clone, PartialEq, Model)]
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(at_least_one_of(value, label, glyph,
        message = "Empty `support:` block. Set at least one of `value`, `label`, or `glyph`, or omit the block entirely."
    )),
    constraint(requires(value, when(set(good)),
        message = "`support.good` needs `support.value`: the direction is read from the value's sign."
    )),
    constraint(at_most_one_of(good, glyph,
        message = "`support.good` and `support.glyph` cannot both be set: `good` picks the glyph from the value's sign. Choose the symbols with `style.support.glyph.up`, `.down`, and `.neutral`."
    ))
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
    constraint(at_most_one_of(good, glyph,
        message = "`good` and the chart-root `glyph` cannot both be set: `good` picks the glyph from the value's sign. Choose the symbols with `style.value.glyph.up`, `.down`, and `.neutral`."
    )),
    constraint(forbids("style.support.tone", when(set("support.good")),
        message = "`support.good` and `style.support.tone` cannot both be set: `good` picks the tone from the value's sign. Remove one."
    ))
)]
pub struct KpiChart {
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
}

/// A card background painted by the value's position in a color scale.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct KpiBackgroundGradient {
    /// Query column whose value positions the color.
    pub column: String,
    /// Color scale the column's value is read against.
    pub scale: ScaleTargetConfig,
}

/// A card background: a color, or a `{column, scale}` gradient by value.
#[derive(Debug, Clone, PartialEq)]
pub enum KpiBackground {
    Color(Color),
    Gradient(<KpiBackgroundGradient as Leaf>::Whole),
}

impl ReadScalar for KpiBackground {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        if matches!(node.kind, model_core::Kind::Map(_)) {
            return KpiBackgroundGradient::whole(node, cx).map(Self::Gradient);
        }
        Color::read(node, cx).map(Self::Color)
    }
}
impl Spell for KpiBackground {
    fn spell(&self) -> serde_json::Value {
        match self {
            Self::Color(c) => c.spell(),
            Self::Gradient(g) => g.spell(),
        }
    }
}

/// The cascade resolves a color slot's tokens through these two: a gradient reads as no token and
/// is left as written, and a resolved token is always a color.
impl AsRef<str> for KpiBackground {
    fn as_ref(&self) -> &str {
        match self {
            Self::Color(c) => c.as_str(),
            Self::Gradient(_) => "",
        }
    }
}
impl TryFrom<String> for KpiBackground {
    type Error = <Color as TryFrom<String>>::Error;
    fn try_from(resolved: String) -> Result<Self, Self::Error> {
        Color::try_from(resolved).map(Self::Color)
    }
}

union_leaf!(
    KpiBackground,
    ::model_core::TypeExpr::Either(
        &::model_core::TypeExpr::Ref(
            "KpiBackgroundGradient",
            <KpiBackgroundGradient as model_core::Model>::info
        ),
        &<Color as Leaf>::EXPR,
    )
);
