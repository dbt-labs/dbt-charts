//! `style/theme/variables.py`: variable controls chrome.
//!
//! `presets` holds daterange preset names. Python checks each against the
//! preset grammar (`parse_daterange_preset`); no Rust parser of that grammar
//! exists yet, so the check stays with the template pass that gains one.

use crate::primitives::BorderStyle;
use crate::string_enum;
use crate::style::font::FontStyle;
use crate::style::page::InputStyle;
use model_core::{Cx, FromNode, Leaf, Merge, Node, ReadScalar, Scalar, TypeExpr};
use model_derive::Model;

string_enum!(
    /// Variables strip placement.
    VariablesPosition { Top = "top", Bottom = "bottom", TitleInline = "title-inline" }
);

/// Per-label font substyle for variable controls.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct VariablesLabelStyle {
    /// Variable label font style overrides.
    #[model(inherit_slot = "Style.variables.font")]
    pub font: FontStyle,
}

/// Per-value font substyle for variable controls.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct VariablesValueStyle {
    /// Variable value font style overrides.
    #[model(inherit_slot = "Style.variables.font")]
    pub font: FontStyle,
}

/// Unselected or hint text in variable inputs, lighter than the selected value.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct VariablesPlaceholderStyle {
    /// Variable placeholder-text font style overrides.
    #[model(inherit_slot = "Style.variables.font")]
    pub font: FontStyle,
}

/// Daterange preset names, at least one. Python's renderer states a variadic tuple as its minimum
/// length alone, with no `type` or `items`.
#[derive(Debug, Clone, PartialEq)]
pub struct Presets(pub Vec<String>);

impl ReadScalar for Presets {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        let items = <Vec<String> as FromNode>::whole(node, cx)?;
        if items.is_empty() {
            cx.invalid(
                node.span,
                "Tuple should have at least 1 item after validation, not 0",
            );
            return None;
        }
        Some(Self(items))
    }
}

impl FromNode for Presets {
    fn authored(node: &Node, cx: &mut Cx) -> Self::Authored {
        Self::read(node, cx)
    }
    fn patch(node: &Node, cx: &mut Cx) -> Self::Patch {
        Self::read(node, cx)
    }
    fn whole(node: &Node, cx: &mut Cx) -> Option<Self::Whole> {
        Self::read(node, cx)
    }
}

impl Leaf for Presets {
    type Whole = Presets;
    type Authored = Option<Presets>;
    type Patch = Option<Presets>;
    const MERGE: Merge = Merge::Override;
    const EXPR: TypeExpr = TypeExpr::Keyed(
        &model_core::Keywords::at_least_items(1),
        &TypeExpr::Scalar(Scalar::Json),
    );
}
model_core::build_leaf!(Presets);

/// Variable controls chrome. `input.background` styles the inputs only: the
/// strip itself is transparent and sits on the board canvas.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct VariablesStyle {
    /// Show the variables control panel.
    pub visible: bool,
    /// Variables strip placement: stacked under the title (top/bottom) or on
    /// one horizontal band with the board title (title-inline).
    pub position: VariablesPosition,
    /// Bottom padding in pixels added below the title-inline band.
    pub title_inline_band_bottom_pad: f64,
    /// Gap between variable controls in pixels.
    pub gap: f64,
    /// Position of labels relative to their input controls (e.g. 'left', 'top').
    pub label_position: String,
    /// When position is title-inline: max title column width in px. 0 means no cap.
    pub title_inline_title_max_width: f64,
    /// Variables panel base font style overrides.
    #[model(inherit_slot = "Style.font")]
    pub font: FontStyle,
    /// Variable label typography.
    pub label: VariablesLabelStyle,
    /// Variable value typography.
    pub value: VariablesValueStyle,
    /// Style for unselected/hint text in variable inputs.
    pub placeholder: VariablesPlaceholderStyle,
    /// Height of the variables panel container in pixels.
    pub container_height: f64,
    /// Variables panel border style.
    pub border: BorderStyle,
    /// Gap between label and input within a single control in pixels.
    pub control_gap: f64,
    /// Input control style.
    pub input: InputStyle,
    /// The preset menu every daterange picker offers, in order; a variable's
    /// own `presets:` replaces it.
    pub presets: Presets,
}
