//! `chart/authored/_base.py`'s `ChartQuery`: where a chart reads its data, as written.
//!
//! A string is a query name or SQL text, never a cross-file ref (`'@str'` in Python: chart-level
//! strings are resolved later, unlike a board's own `queries`). A mapping with a `ref` key is a
//! [`QueryRef`], and any other mapping is an inline [`Query`]. Normalize hoists each of the three
//! into a `QueryId` (D-05), which is the fourth arm: the one a normalized chart holds, never read
//! from YAML.

use crate::ids::{QueryId, QueryRef};
use crate::query::Query;
use model_core::{Cx, FromNode, Leaf, Model, Node, ReadScalar, Scalar, TypeExpr};
use yaml::{Kind, Value};

/// A chart's data link.
#[derive(Debug, Clone, PartialEq)]
pub enum ChartQuery {
    /// A query's name, or SQL text.
    Name(String),
    /// A query defined in place.
    Inline(Box<<Query as Leaf>::Whole>),
    /// A query of another file.
    Ref(<QueryRef as Leaf>::Whole),
    /// The hoisted query: its origin file and name.
    Hoisted(QueryId),
}

impl ReadScalar for ChartQuery {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        match &node.kind {
            Kind::Scalar(Value::Str(_)) => String::read(node, cx).map(Self::Name),
            Kind::Map(m) if m.contains_key("ref") => {
                <QueryRef as FromNode>::whole(node, cx).map(Self::Ref)
            }
            Kind::Map(_) => <Query as FromNode>::whole(node, cx).map(|q| Self::Inline(Box::new(q))),
            _ => {
                cx.mismatch(node, "a query name, an inline query or a {ref: ..}");
                None
            }
        }
    }
}

union_leaf!(
    ChartQuery,
    TypeExpr::Either(
        &TypeExpr::Ref("Query", <Query as Model>::info),
        &TypeExpr::Either(&<QueryRef as Leaf>::EXPR, &TypeExpr::Scalar(Scalar::Str))
    )
);

/// A layer's data link (`TypedLayerBase.query`): a query name only, where a chart also takes an
/// inline query or a ref. Normalize hoists it into the `QueryId`, which is the second arm.
#[derive(Debug, Clone, PartialEq)]
pub enum LayerQuery {
    /// A query's name.
    Name(String),
    /// The hoisted query: its origin file and name.
    Hoisted(QueryId),
}

impl ReadScalar for LayerQuery {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        String::read(node, cx).map(Self::Name)
    }
}

union_leaf!(LayerQuery, TypeExpr::Scalar(Scalar::Str));
