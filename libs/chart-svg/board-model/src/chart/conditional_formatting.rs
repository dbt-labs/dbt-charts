//! `models/conditional_predicate.py`: the predicate half of a conditional rule.
//!
//! A rule is one predicate (`eq`, `lt`, `between`, `in`, `is_null`, `default`, ...) and the style
//! it applies, which each family's rule model carries beside the flattened [`Predicate`]. Exactly
//! one predicate is a `constraint` of those models. Whether a `default` rule is last is the one
//! positional rule, checked by each family's `default_position`.
//!
//! Matching a row against a rule is `bind`'s (`match_predicate`); this model only holds the rule.

use super::annotation::{
    aggregate_named, call, mapping, repr, AggregateAnchor, AggregateFn, AGGREGATE_FUNCTIONS,
};
use model_core::{Cx, FromNode, Kind, Leaf, Node, ReadScalar, ScalarValue as Value};
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

/// An aggregate of the rule's column, `avg()` or `percentile(25)`, which resolve computes.
pub type Aggregate = <AggregateAnchor as Leaf>::Whole;

/// `text` read as an aggregate call; `Ok(None)` when it is no call, `Err` once an error is recorded.
fn aggregate_call(node: &Node, text: &str, cx: &mut Cx) -> Result<Option<Aggregate>, ()> {
    let Some((name, arg)) = call(text) else {
        return Ok(None);
    };
    let fields = match aggregate_named(name, arg) {
        Ok(Some(fields)) => fields,
        Ok(None) => {
            cx.invalid(
                node.span,
                format!(
                    "Value error, {} is not an aggregate function; a rule takes {AGGREGATE_FUNCTIONS}",
                    repr(text.trim())
                ),
            );
            return Err(());
        }
        Err(msg) => {
            cx.invalid(node.span, format!("Value error, {msg}"));
            return Err(());
        }
    };
    <AggregateAnchor as FromNode>::whole(&mapping(node, fields), cx)
        .map(Some)
        .ok_or(())
}

/// What `eq` and `ne` compare a cell with: a value as written, or the column's `min()` or `max()`.
#[derive(Debug, Clone, PartialEq)]
pub enum Operand {
    Value(serde_json::Value),
    Extreme(Aggregate),
}

impl ReadScalar for Operand {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        let Kind::Scalar(Value::Str(text)) = &node.kind else {
            return Some(Self::Value(node.to_json()));
        };
        match aggregate_call(node, text, cx) {
            Ok(None) => Some(Self::Value(node.to_json())),
            Ok(Some(a)) if matches!(a.r#fn, Some(AggregateFn::Min | AggregateFn::Max)) => {
                Some(Self::Extreme(a))
            }
            Ok(Some(_)) => {
                cx.invalid(
                    node.span,
                    "Value error, eq and ne take min() or max(); compare with avg(), median() or percentile() through lt, lte, gt, gte or between",
                );
                None
            }
            Err(()) => None,
        }
    }
}
impl ::model_core::Spell for Operand {
    fn spell(&self) -> ::serde_json::Value {
        match self {
            Self::Value(v) => v.clone(),
            Self::Extreme(a) => ::model_core::Spell::spell(a),
        }
    }
}

union_leaf!(
    Operand,
    ::model_core::TypeExpr::Scalar(::model_core::Scalar::Json)
);

/// A literal comparison operand: a number, or a date compared as one.
#[derive(Debug, Clone, PartialEq)]
pub enum Threshold {
    Number(f64),
    /// A YAML date or timestamp, or ISO text, as written, and that instant as days since the epoch.
    Date { text: String, day: f64 },
}

impl Threshold {
    /// What the operand orders by: the number, or the date's day.
    pub fn key(&self) -> f64 {
        match self {
            Self::Number(n) => *n,
            Self::Date { day, .. } => *day,
        }
    }
}

impl ReadScalar for Threshold {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        // Only a full `YYYY-MM-DD…` is a date: pydantic reads a quoted `"2026"` as a number.
        if let Kind::Scalar(Value::Timestamp(text) | Value::Str(text)) = &node.kind {
            if let Some(day) = d3rs::calendar::date_text_day(text).filter(|_| text.len() >= 10) {
                return Some(Self::Date { text: text.clone(), day });
            }
        }
        crate::unit::Number::read(node, cx).map(|n| Self::Number(n.get()))
    }
}
impl ::model_core::Spell for Threshold {
    fn spell(&self) -> ::serde_json::Value {
        match self {
            Self::Number(n) => n.spell(),
            Self::Date { text, .. } => text.spell(),
        }
    }
}

const NUMBER: ::model_core::TypeExpr = ::model_core::TypeExpr::Either(
    &::model_core::TypeExpr::Scalar(::model_core::Scalar::Int),
    &::model_core::TypeExpr::Scalar(::model_core::Scalar::Float),
);
const DATE: ::model_core::TypeExpr = ::model_core::TypeExpr::Either(
    &::model_core::TypeExpr::Scalar(::model_core::Scalar::Date),
    &::model_core::TypeExpr::Scalar(::model_core::Scalar::DateTime),
);
const THRESHOLD: ::model_core::TypeExpr = ::model_core::TypeExpr::Either(&NUMBER, &DATE);

union_leaf!(Threshold, THRESHOLD);

/// A bound: a number or date, or an aggregate of the rule's column.
#[derive(Debug, Clone, PartialEq)]
pub enum Bound {
    Threshold(Threshold),
    Aggregate(Aggregate),
}

impl ReadScalar for Bound {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        match &node.kind {
            Kind::Scalar(Value::Str(text)) => match aggregate_call(node, text, cx) {
                Ok(Some(a)) => Some(Self::Aggregate(a)),
                Ok(None) => Threshold::read(node, cx).map(Self::Threshold),
                Err(()) => None,
            },
            Kind::Map(_) => <AggregateAnchor as FromNode>::whole(node, cx).map(Self::Aggregate),
            _ => Threshold::read(node, cx).map(Self::Threshold),
        }
    }
}
impl ::model_core::Spell for Bound {
    fn spell(&self) -> ::serde_json::Value {
        match self {
            Self::Threshold(t) => ::model_core::Spell::spell(t),
            Self::Aggregate(a) => ::model_core::Spell::spell(a),
        }
    }
}

union_leaf!(
    Bound,
    ::model_core::TypeExpr::Either(&THRESHOLD, &::model_core::TypeExpr::Scalar(::model_core::Scalar::Str))
);

/// `between: [low, high]`, inclusive: two numbers or two dates, low first, or aggregates.
#[derive(Debug, Clone, PartialEq)]
pub struct Between(pub [Bound; 2]);

impl ReadScalar for Between {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        let items = <Vec<Bound> as FromNode>::whole(node, cx)?;
        let Ok(pair) = <[Bound; 2]>::try_from(items.clone()) else {
            let side = if items.len() < 2 { "at least" } else { "at most" };
            cx.invalid(
                node.span,
                format!("List should have {side} 2 items after validation, not {}", items.len()),
            );
            return None;
        };
        if let [Bound::Threshold(low), Bound::Threshold(high)] = &pair {
            if matches!(low, Threshold::Number(_)) != matches!(high, Threshold::Number(_)) {
                cx.invalid(node.span, "Value error, between needs two numbers or two dates, not a mix");
                return None;
            }
            if low.key() > high.key() {
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
        }
        Some(Self(pair))
    }
}
impl ::model_core::Spell for Between {
    fn spell(&self) -> ::serde_json::Value {
        ::serde_json::Value::Array(self.0.iter().map(::model_core::Spell::spell).collect())
    }
}

union_leaf!(
    Between,
    ::model_core::TypeExpr::Keyed(
        &::model_core::Keywords::exactly_items(2),
        &::model_core::TypeExpr::List(&<Bound as ::model_core::Leaf>::EXPR),
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
    /// Match rows where the column value equals this value, or the column's min() or max().
    #[model(absent = "no equality test")]
    pub eq: Option<Operand>,
    /// Match rows where the column value does not equal this value, or the column's min() or max().
    #[model(absent = "no inequality test")]
    pub ne: Option<Operand>,
    /// Match rows where the column value is less than this number, date or aggregate.
    #[model(absent = "no less-than test")]
    pub lt: Option<Bound>,
    /// Match rows where the column value is less than or equal to this number, date or aggregate.
    #[model(absent = "no less-or-equal test")]
    pub lte: Option<Bound>,
    /// Match rows where the column value is greater than this number, date or aggregate.
    #[model(absent = "no greater-than test")]
    pub gt: Option<Bound>,
    /// Match rows where the column value is greater than or equal to this number, date or aggregate.
    #[model(absent = "no greater-or-equal test")]
    pub gte: Option<Bound>,
    /// Match rows where the column value falls in [low, high] (inclusive); a bound may be an aggregate.
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
