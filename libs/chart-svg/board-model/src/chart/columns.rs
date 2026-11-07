//! The column-valued and small numeric leaves the chart mixins share: a channel column that is
//! never a literal color, a one-or-many column list, and fixed-size number lists.

use crate::facet::{facet, Channel, Facet};
use model_core::{Cx, FromNode, Node, ReadScalar};

const NOT_A_COLOR: &str = r"^(?:[^#]|$)";

fn bare_column(v: &str) -> Result<String, String> {
    if v.starts_with('#') {
        return Err(format!("String should match pattern '{NOT_A_COLOR}'"));
    }
    Ok(v.to_string())
}

facet!(
    /// A column that carries a data channel (`color:`, a layer's `color:`): a bare field name. A
    /// literal color such as `#fff` is paint, and belongs under `style`.
    ColorColumn = "channel",
    bare_column,
    model_core::Keywords::pattern(NOT_A_COLOR)
);
facet!(
    /// The column a `pattern:` channel reads: a bare field name.
    PatternColumn = "channel",
    bare_column,
    model_core::Keywords::pattern(NOT_A_COLOR)
);

fn non_blank(v: &str) -> Result<String, String> {
    if v.chars().all(char::is_whitespace) {
        return Err(r"String should match pattern '\S'".to_string());
    }
    Ok(v.to_string())
}

facet!(
    /// A column name with a character other than whitespace.
    NonBlankColumn = "channel",
    non_blank,
    model_core::Keywords::pattern(r"\S")
);

/// One column, or a list of them (`y: revenue` / `y: [revenue, margin]`).
#[derive(Debug, Clone, PartialEq)]
pub enum Columns {
    One(Channel),
    Many(Vec<Channel>),
}

impl Columns {
    pub fn len(&self) -> usize {
        match self {
            Self::One(_) => 1,
            Self::Many(c) => c.len(),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Names no column: an empty list, or a single empty name (Python's `nonempty` on `y`).
    pub fn is_blank(&self) -> bool {
        match self {
            Self::One(c) => c.as_str().is_empty(),
            Self::Many(c) => c.is_empty(),
        }
    }
}

impl ReadScalar for Columns {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        if matches!(node.kind, model_core::Kind::Seq(_)) {
            return <Vec<Channel> as FromNode>::whole(node, cx).map(Self::Many);
        }
        Channel::read(node, cx).map(Self::One)
    }
}
union_leaf!(
    Columns,
    ::model_core::TypeExpr::Facet("columns", ::model_core::Scalar::Str)
);

macro_rules! number_list {
    ($(#[$m:meta])* $name:ident, $n:literal, $what:literal, $expr:expr) => {
        $(#[$m])*
        #[derive(Debug, Clone, Copy, PartialEq)]
        pub struct $name(pub [f64; $n]);

        impl ReadScalar for $name {
            fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
                let items = <Vec<f64> as FromNode>::whole(node, cx)?;
                match <[f64; $n]>::try_from(items.as_slice()) {
                    Ok(a) => Some(Self(a)),
                    Err(_) => {
                        let side = if items.len() < $n { "at least" } else { "at most" };
                        cx.invalid(
                            node.span,
                            format!(
                                concat!($what, " should have {} ", $n, " items after validation, not {}"),
                                side,
                                items.len()
                            ),
                        );
                        None
                    }
                }
            }
        }
        union_leaf!($name, $expr);
    };
}

number_list!(
    /// A list of exactly two numbers.
    NumberPair, 2, "List",
    ::model_core::TypeExpr::Keyed(
        &::model_core::Keywords::exactly_items(2),
        &::model_core::TypeExpr::List(&::model_core::TypeExpr::Scalar(::model_core::Scalar::Float)),
    )
);
number_list!(
    /// A `[longitude, latitude]` or `[x, y]` tuple.
    LonLat, 2, "Tuple",
    ::model_core::TypeExpr::Tuple(&[
        &::model_core::TypeExpr::Scalar(::model_core::Scalar::Float),
        &::model_core::TypeExpr::Scalar(::model_core::Scalar::Float),
    ])
);
