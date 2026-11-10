//! `chart/authored/spark_bar.py`: compact horizontal bars.

use super::SharedChartFields;
use crate::facet::Channel;
use crate::style::spark_bar::SparkBarChartStyle;
use model_core::{Cx, FromNode, Kind, Node, ReadScalar, Spell};
use model_derive::Model;

/// The bar-label column: a column name, or a list holding exactly one (one row of bars has no
/// second encoding to separate a second series by).
#[derive(Debug, Clone, PartialEq)]
pub struct LabelColumn(pub Channel);

impl ReadScalar for LabelColumn {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        if !matches!(node.kind, Kind::Seq(_)) {
            return Channel::read(node, cx).map(Self);
        }
        let items = <Vec<Channel> as FromNode>::whole(node, cx)?;
        let mut it = items.into_iter();
        match (it.next(), it.next()) {
            (Some(c), None) => Some(Self(c)),
            (first, _) => {
                let side = if first.is_none() {
                    "at least"
                } else {
                    "at most"
                };
                cx.invalid(
                    node.span,
                    format!("List should have {side} 1 item after validation"),
                );
                None
            }
        }
    }
}
impl Spell for LabelColumn {
    fn spell(&self) -> serde_json::Value {
        self.0.spell()
    }
}

union_leaf!(
    LabelColumn,
    ::model_core::TypeExpr::Either(
        &::model_core::TypeExpr::Scalar(::model_core::Scalar::Str),
        &::model_core::TypeExpr::Keyed(
            &::model_core::Keywords {
                min_items: Some(1),
                max_items: Some(1),
                ..::model_core::Keywords::NONE
            },
            &::model_core::TypeExpr::List(&::model_core::TypeExpr::Scalar(
                ::model_core::Scalar::Str
            )),
        ),
    )
);

/// Compact horizontal bars against one label column.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SparkBarChart {
    #[flatten]
    pub shared: SharedChartFields,
    /// Bar-magnitude (numeric) column name.
    #[model(absent = "auto-detected from the query columns")]
    pub x: Option<Channel>,
    /// Bar-label (category) column name(s).
    #[model(absent = "auto-detected from the query columns")]
    pub y: Option<LabelColumn>,
    /// Appearance overrides for this chart alone.
    #[model(fill = fill_style, nullable)]
    pub style: SparkBarChartStyle,
}
