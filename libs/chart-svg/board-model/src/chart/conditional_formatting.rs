//! `chart/authored/_conditional_formatting.py`: predicate rules scoped to a column.
//!
//! A rule is one predicate (`eq`, `lt`, `between`, `in`, `is_null`, `default`, ...) and the style
//! it applies. Exactly one predicate and at least one style override are `constraint`s. `font.weight`
//! is a `FontWeightValue`, which refuses what is no CSS weight. Whether a `default` rule is last is
//! the one positional rule: [`FieldConditionalFormatting::default_position`].
//!
//! Matching a row against a rule is `bind`'s (`match_predicate`); this model only holds the rule.

use super::NumberPair;
use crate::facet::Color;
use crate::facet::FontWeightValue;
use crate::primitives::{GlyphText, Tone};
use crate::style::font::FontStyle;
use diagnostic::diag;
use diagnostic::Diagnostic;
use model_core::{
    Authored, Cx, FromNode, Kind, Node, Patch, ReadScalar, ScalarValue as Value, Tri,
};
use model_derive::Model;

/// A value a rule compares a cell with: any scalar or list as written.
#[derive(Debug, Clone, PartialEq)]
pub struct Cell(pub serde_json::Value);

impl ReadScalar for Cell {
    fn read(node: &Node, _: &mut Cx) -> Option<Self> {
        Some(Self(node.to_json()))
    }
}
union_leaf!(
    Cell,
    ::model_core::TypeExpr::Facet("any", ::model_core::Scalar::Str)
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
union_leaf!(
    Cells,
    ::model_core::TypeExpr::Facet("any_list", ::model_core::Scalar::Str)
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
union_leaf!(
    CatchAll,
    ::model_core::TypeExpr::Facet("true", ::model_core::Scalar::Bool)
);

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
    pub lt: Option<f64>,
    /// Match rows where the column value is less than or equal to this number.
    #[model(absent = "no less-or-equal test")]
    pub lte: Option<f64>,
    /// Match rows where the column value is greater than this number.
    #[model(absent = "no greater-than test")]
    pub gt: Option<f64>,
    /// Match rows where the column value is greater than or equal to this number.
    #[model(absent = "no greater-or-equal test")]
    pub gte: Option<f64>,
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

/// A single conditional formatting rule.
#[derive(Debug, Clone, PartialEq, Model)]
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(at_least_one_of(eq, ne, lt, lte, gt, gte, between, "in", is_null, default)),
    constraint(at_most_one_of(eq, ne, lt, lte, gt, gte, between, "in", is_null, default)),
    constraint(requires(glyph, when(set(glyph_color)))),
    constraint(requires(glyph, when(set(tone)))),
    constraint(at_least_one_of(
        background,
        "font.extends",
        "font.color",
        "font.weight",
        "font.style",
        "font.decoration",
        glyph
    ))
)]
pub struct ConditionalRule {
    #[flatten]
    pub predicate: Predicate,
    /// Cell background color applied when the rule matches.
    #[model(absent = "no background override")]
    pub background: Option<String>,
    /// Font style overrides (color, weight, style, decoration) applied when the rule matches.
    #[model(absent = "no font override", local)]
    pub font: Option<Patch<FontStyle>>,
    /// Text shown before the cell value when the rule matches.
    #[model(absent = "no glyph")]
    pub glyph: Option<GlyphText>,
    /// Color for the glyph when the rule matches. Requires glyph to be set.
    #[model(absent = "the glyph takes the tone's color, else the text's")]
    pub glyph_color: Option<Color>,
    /// Semantic tone (positive|negative|warning|info) that colors the glyph via the theme's tone palette; the preferred, theme-adaptive alternative to a raw glyph_color. Requires glyph. Explicit glyph_color wins.
    #[model(absent = "no tone")]
    pub tone: Option<Tone>,
}

/// Conditional formatting rules scoped to a single column.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct FieldConditionalFormatting {
    /// Ordered list of conditional rules. The first matching rule applies; a 'default: true' rule must be last.
    pub when: Vec<ConditionalRule>,
}

impl FieldConditionalFormatting {
    /// A `default: true` rule is allowed only last in a `when` list: a positional rule over an
    /// element field, which no `constraint` kind tests.
    pub fn default_position(field: &Authored<FieldConditionalFormatting>) -> Vec<Diagnostic> {
        let rules = field.when.as_deref().unwrap_or_default();
        rules
            .iter()
            .enumerate()
            .filter(|(i, r)| matches!(r.predicate.default, Tri::Value(_)) && i + 1 != rules.len())
            .map(|(i, _)| {
                diag::err_validation_field(
                    format!("when[{i}]"),
                    format!(
                        "Value error, ConditionalRule with default: true must be the last entry in a when list (found at position {i} of {})",
                        rules.len()
                    ),
                )
            })
            .collect()
    }
}

impl ConditionalRule {
    /// `font.weight` must be a CSS font-weight: `normal`, `bold` or 100 to 900 in hundreds.
    /// The weight facet takes any name, since a theme font may use others, so a rule's own
    /// override is where this narrower set is checked.
    pub fn font_weight(rule: &Authored<ConditionalRule>) -> Vec<Diagnostic> {
        let Tri::Value(font) = &rule.font else {
            return Vec::new();
        };
        let (shown, valid) = match &font.weight {
            Some(FontWeightValue::Name(n)) => (
                diagnostic::py_repr(&serde_json::Value::String(n.clone())),
                n == "normal"
                    || n == "bold"
                    || matches!(n.parse::<u32>(), Ok(100..=900))
                        && n.len() == 3
                        && n.ends_with("00"),
            ),
            Some(FontWeightValue::Number(x)) => (
                format!("{x:?}"),
                x.fract() == 0.0 && (100.0..=900.0).contains(x) && (*x as u32).is_multiple_of(100),
            ),
            None => return Vec::new(),
        };
        if valid {
            return Vec::new();
        }
        vec![diag::err_validation_field(
            "font.weight",
            format!("Value error, ConditionalRule font.weight {shown} is not a valid CSS font-weight; use a numeric string (e.g. '700') or 'normal'/'bold'."),
        )]
    }
}
