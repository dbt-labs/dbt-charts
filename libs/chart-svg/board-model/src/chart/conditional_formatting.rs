//! `models/conditional_predicate.py`: the predicate half of a conditional rule.
//!
//! A rule is one predicate (`eq`, `lt`, `between`, `in`, `is_null`, `default`, ...) and the style
//! it applies, which each family's rule model carries beside the flattened [`Predicate`]. Exactly
//! one predicate is a `constraint` of those models. Whether a `default` rule is last is the one
//! positional rule, checked by each family's `default_position`.
//!
//! Matching a row against a rule is `bind`'s (`match_predicate`); this model only holds the rule.

use super::NumberPair;
use model_core::{Cx, FromNode, Kind, Node, ReadScalar, ScalarValue as Value};
use model_derive::Model;

/// A value a rule compares a cell with: any scalar or list as written.
#[derive(Debug, Clone, PartialEq)]
pub struct Cell(pub serde_json::Value);

impl ReadScalar for Cell {
    fn read(node: &Node, _: &mut Cx) -> Option<Self> {
        Some(Self(node.to_json()))
    }
}
impl ::model_core::Spell for Cell {
    fn spell(&self) -> ::serde_json::Value {
        self.0.clone()
    }
}

union_leaf!(
    Cell,
    ::model_core::TypeExpr::Scalar(::model_core::Scalar::Json)
);

/// The values of an `in` rule: at least one.
#[derive(Debug, Clone, PartialEq)]
pub struct Cells(pub Vec<Cell>);

impl ReadScalar for Cells {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        let cells = <Vec<Cell> as FromNode>::whole(node, cx)?;
        if cells.is_empty() {
            cx.invalid(
                node.span,
                "List should have at least 1 item after validation, not 0",
            );
            return None;
        }
        Some(Self(cells))
    }
}
impl ::model_core::Spell for Cells {
    fn spell(&self) -> ::serde_json::Value {
        ::model_core::Spell::spell(&self.0)
    }
}

union_leaf!(
    Cells,
    ::model_core::TypeExpr::Keyed(
        &::model_core::Keywords::at_least_items(1),
        &::model_core::TypeExpr::List(&::model_core::TypeExpr::Scalar(::model_core::Scalar::Json)),
    )
);

/// `between: [low, high]`, inclusive, low first.
#[derive(Debug, Clone, PartialEq)]
pub struct Between(pub NumberPair);

impl ReadScalar for Between {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        let pair = NumberPair::read(node, cx)?;
        if pair.0[0] > pair.0[1] {
            let text = match &node.kind {
                Kind::Seq(items) => items
                    .iter()
                    .map(|i| i.to_json().to_string())
                    .collect::<Vec<_>>(),
                _ => Vec::new(),
            };
            cx.invalid(
                node.span,
                format!(
                    "Value error, between requires low <= high, got [{}]",
                    text.join(", ")
                ),
            );
            return None;
        }
        Some(Self(pair))
    }
}
impl ::model_core::Spell for Between {
    fn spell(&self) -> ::serde_json::Value {
        ::model_core::Spell::spell(&self.0)
    }
}

union_leaf!(
    Between,
    ::model_core::TypeExpr::Keyed(
        &::model_core::Keywords::exactly_items(2),
        &::model_core::TypeExpr::List(&::model_core::TypeExpr::Either(
            &::model_core::TypeExpr::Scalar(::model_core::Scalar::Int),
            &::model_core::TypeExpr::Scalar(::model_core::Scalar::Float),
        )),
    )
);

/// `default: true`, the only value a catch-all rule takes.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct CatchAll;

impl ReadScalar for CatchAll {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        if matches!(&node.kind, Kind::Scalar(Value::Bool(true))) {
            return Some(Self);
        }
        cx.invalid(node.span, "Input should be True");
        None
    }
}
impl ::model_core::Spell for CatchAll {
    fn spell(&self) -> ::serde_json::Value {
        true.into()
    }
}

union_leaf!(CatchAll, ::model_core::TypeExpr::Const(true));

/// The predicate fields of a rule; exactly one is set.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct Predicate {
    /// Match rows where the column value equals this value.
    #[model(absent = "no equality test")]
    pub eq: Option<Cell>,
    /// Match rows where the column value does not equal this value.
    #[model(absent = "no inequality test")]
    pub ne: Option<Cell>,
    /// Match rows where the column value is less than this number.
    #[model(absent = "no less-than test")]
    pub lt: Option<crate::unit::Number>,
    /// Match rows where the column value is less than or equal to this number.
    #[model(absent = "no less-or-equal test")]
    pub lte: Option<crate::unit::Number>,
    /// Match rows where the column value is greater than this number.
    #[model(absent = "no greater-than test")]
    pub gt: Option<crate::unit::Number>,
    /// Match rows where the column value is greater than or equal to this number.
    #[model(absent = "no greater-or-equal test")]
    pub gte: Option<crate::unit::Number>,
    /// Match rows where the column value falls in [low, high] (inclusive).
    #[model(absent = "no range test")]
    pub between: Option<Between>,
    /// Match rows where the column value is in this list.
    #[model(absent = "no membership test")]
    pub r#in: Option<Cells>,
    /// Match null rows (true) or non-null rows (false).
    #[model(absent = "no null test")]
    pub is_null: Option<bool>,
    /// Catch-all rule that matches any row not matched by earlier rules. Must be the last entry.
    #[model(absent = "not a catch-all")]
    pub default: Option<CatchAll>,
}
