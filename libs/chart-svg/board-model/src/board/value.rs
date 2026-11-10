//! The values a board field takes that are not one scalar: sizes, a visibility
//! condition, and the `extends` list.
//!
//! Each reads as Python's `str | int` or `bool | str | probe` unions do, and a value
//! none of the arms takes is an error at the node. A size must be positive, as
//! `normalize/layout.py`'s `_validate_dimension` requires.

use crate::unit::Px;
use model_core::{
    build_leaf, Build, Cx, FromNode, Leaf, Merge, Node, ReadScalar, Scalar, Spell, TypeExpr, Unset,
};
use model_derive::Model;
use yaml::{Kind, Value};

/// Implements `FromNode` for a scalar-shaped union leaf that already has `ReadScalar`.
macro_rules! scalar_leaf {
    ($t:ty, $expr:expr) => {
        impl Leaf for $t {
            type Whole = $t;
            type Authored = Option<$t>;
            type Patch = Option<$t>;
            const MERGE: Merge = Merge::Override;
            const EXPR: TypeExpr = $expr;
        }
        build_leaf!($t);
        impl FromNode for $t {
            fn authored(node: &Node, cx: &mut Cx) -> Self::Authored {
                <$t as ReadScalar>::read(node, cx)
            }
            fn patch(node: &Node, cx: &mut Cx) -> Self::Patch {
                <$t as ReadScalar>::read(node, cx)
            }
            fn whole(node: &Node, cx: &mut Cx) -> Option<Self::Whole> {
                <$t as ReadScalar>::read(node, cx)
            }
        }
    };
}

/// A size written as a number of pixels, `"400"`, `"400px"` or `"50%"`.
fn dimension(node: &Node) -> Option<Result<(f64, bool), String>> {
    let Kind::Scalar(v) = &node.kind else {
        return None;
    };
    let text = match v {
        Value::Number(n) => return n.as_f64().map(|f| Ok((f, false))),
        Value::Str(s) => s.trim(),
        _ => return None,
    };
    let (digits, percent) = match (text.strip_suffix('%'), text.strip_suffix("px")) {
        (Some(d), _) => (d, true),
        (None, Some(d)) => (d, false),
        (None, None) => (text, false),
    };
    Some(match digits.trim().parse::<f64>() {
        Ok(f) if f.is_finite() => Ok((f, percent)),
        _ => Err(format!(
            "{text:?} is not a valid dimension (use e.g. '400', '400px' or '50%')"
        )),
    })
}

/// A size on a board nested in a parent's layout: pixels, or a share of the parent.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Length {
    Px(Px),
    /// A percentage of the parent: `50` is half.
    Percent(f64),
}

impl ReadScalar for Length {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        let Some(read) = dimension(node) else {
            cx.mismatch(node, "a number of pixels or a size such as '50%'");
            return None;
        };
        let (value, percent) = match read {
            Ok(v) => v,
            Err(e) => {
                cx.invalid(node.span, e);
                return None;
            }
        };
        if value <= 0.0 {
            cx.invalid(node.span, format!("{value} must be positive"));
            return None;
        }
        if percent {
            return Some(Self::Percent(value));
        }
        Px::try_from(value)
            .map(Self::Px)
            .map_err(|e| cx.invalid(node.span, e))
            .ok()
    }
}
impl Spell for Length {
    fn spell(&self) -> serde_json::Value {
        match self {
            Self::Px(px) => px.spell(),
            Self::Percent(p) => format!("{p}%").into(),
        }
    }
}
scalar_leaf!(
    Length,
    TypeExpr::Either(
        &TypeExpr::Scalar(Scalar::Float),
        &TypeExpr::Scalar(Scalar::Str)
    )
);

/// A root board's own exact width in pixels, past `style.frame.max_width` if it says so.
/// There is no parent to take a share of, so a percentage is refused.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Width(f64);

impl Width {
    pub fn px(self) -> f64 {
        self.0
    }
}

impl TryFrom<f64> for Width {
    type Error = String;
    fn try_from(v: f64) -> Result<Self, String> {
        if v > 0.0 && v.is_finite() {
            Ok(Self(v))
        } else {
            Err(format!("{v} must be positive"))
        }
    }
}

impl ReadScalar for Width {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        let Some(read) = dimension(node) else {
            cx.mismatch(node, "a number of pixels such as '900' or '900px'");
            return None;
        };
        let made = match read {
            Ok((_, true)) => Err(
                "cannot be a percentage on the root board: there is no parent to size it \
                 relative to. Use pixels (e.g. '900' or '900px')."
                    .to_string(),
            ),
            Ok((v, false)) => Self::try_from(v),
            Err(e) => Err(e),
        };
        made.map_err(|e| cx.invalid(node.span, e)).ok()
    }
}
impl Spell for Width {
    fn spell(&self) -> serde_json::Value {
        self.0.spell()
    }
}
scalar_leaf!(
    Width,
    TypeExpr::Either(
        &TypeExpr::Scalar(Scalar::Float),
        &TypeExpr::Scalar(Scalar::Str)
    )
);

/// The boards a board inherits from: built-in theme names and `./` file paths, the last
/// winning. One name or a list.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Extends(pub Vec<String>);

impl ReadScalar for Extends {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        match &node.kind {
            Kind::Scalar(Value::Str(s)) => Some(Self(vec![s.clone()])),
            Kind::Seq(_) => <Vec<String> as FromNode>::whole(node, cx).map(Self),
            _ => {
                cx.mismatch(node, "a theme or board name, or a list of them");
                None
            }
        }
    }
}
impl Spell for Extends {
    fn spell(&self) -> serde_json::Value {
        self.0.spell()
    }
}
scalar_leaf!(
    Extends,
    TypeExpr::Either(
        &TypeExpr::Scalar(Scalar::Str),
        &TypeExpr::List(&TypeExpr::Scalar(Scalar::Str))
    )
);

/// A query read for one boolean cell: the single row of `query` and its `column`.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SingleRowBoolProbe {
    /// Name of the query to execute.
    pub query: String,
    /// Column in the single result row holding the boolean value.
    pub column: String,
}

/// Whether a layout item is drawn: always, never, a variable or Jinja expression read at
/// render time, or a single-row boolean probe.
#[derive(Debug, Clone, PartialEq)]
pub enum Visible {
    Flag(bool),
    /// A variable name (truthy check) or a Jinja boolean expression.
    Expression(String),
    Probe(SingleRowBoolProbe),
}

/// `Visible` as parsed: the probe is its authored twin.
#[derive(Debug, Clone, PartialEq)]
pub enum VisibleAuthored {
    Flag(bool),
    Expression(String),
    Probe(<SingleRowBoolProbe as Leaf>::Whole),
}

impl Spell for VisibleAuthored {
    fn spell(&self) -> serde_json::Value {
        match self {
            Self::Flag(b) => b.spell(),
            Self::Expression(e) => e.spell(),
            Self::Probe(p) => p.spell(),
        }
    }
}

impl Leaf for Visible {
    type Whole = VisibleAuthored;
    type Authored = Option<VisibleAuthored>;
    type Patch = Option<VisibleAuthored>;
    const MERGE: Merge = Merge::Override;
    const EXPR: TypeExpr = TypeExpr::Either(
        &<SingleRowBoolProbe as Leaf>::EXPR,
        &TypeExpr::Either(
            &TypeExpr::Scalar(Scalar::Bool),
            &TypeExpr::Scalar(Scalar::Str),
        ),
    );
}

impl Build for Visible {
    fn build(slot: Self::Patch, at: &str) -> Result<Self, Unset> {
        Self::build_authored(slot, at)
    }
    fn build_authored(slot: Self::Authored, at: &str) -> Result<Self, Unset> {
        match slot {
            None => Err(Unset {
                paths: vec![at.to_string()],
            }),
            Some(VisibleAuthored::Flag(b)) => Ok(Self::Flag(b)),
            Some(VisibleAuthored::Expression(s)) => Ok(Self::Expression(s)),
            Some(VisibleAuthored::Probe(p)) => {
                model_core::build_payload::<SingleRowBoolProbe>(p, at).map(Self::Probe)
            }
        }
    }
}

impl FromNode for Visible {
    fn authored(node: &Node, cx: &mut Cx) -> Self::Authored {
        Self::whole(node, cx)
    }
    fn patch(node: &Node, cx: &mut Cx) -> Self::Patch {
        Self::whole(node, cx)
    }
    fn whole(node: &Node, cx: &mut Cx) -> Option<Self::Whole> {
        match &node.kind {
            Kind::Scalar(Value::Bool(b)) => Some(VisibleAuthored::Flag(*b)),
            Kind::Scalar(Value::Str(s)) => Some(VisibleAuthored::Expression(s.clone())),
            Kind::Map(_) => {
                <SingleRowBoolProbe as FromNode>::whole(node, cx).map(VisibleAuthored::Probe)
            }
            _ => {
                cx.mismatch(node, "a boolean, a variable name or expression, or a probe");
                None
            }
        }
    }
    fn walk(w: &mut model_core::Walk) {
        <SingleRowBoolProbe as FromNode>::walk(w);
    }
}
