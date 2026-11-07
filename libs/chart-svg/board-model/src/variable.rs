//! `variable/authored.py`: variable declarations.
//!
//! A variable is declared once, board-wide, and read by queries and charts. The spine
//! holds the declaration only: types, defaults, presets, `visible`. Resolved values
//! (daterange presets turned into dates, defaults, input refinement) are not here; they
//! come from `plan` (spec § 14).
//!
//! - `input: auto` is [`Engine`]'s `Auto` rule over the concrete [`InputType`]; the board's
//!   data and `options` decide which control `auto` is.
//! - `query` and `options.query` name a query. An inline `query:` definition is hoisted by
//!   the refs pass into the board's `queries` and the variable keeps its name; that
//!   accept arm belongs with the board's `QueryOrRef`, not here.
//! - `presets` holds daterange preset names. Python checks each against the preset grammar
//!   (`parse_daterange_preset`); no Rust parser of that grammar exists yet, so the check stays
//!   with the template pass that gains one (as for `style::variables`).
//! - `min`, `max` and `step` are `f64`: YAML `.inf` and `.nan` are refused when the `Node`
//!   is built, which is `_bounds_are_finite`.
//!
//! Left off the spine: `input_auto_detected`, `authored_fields`, `defined_in_other_file`,
//! `variable_dependencies` (derived: `BoardIndex`) and `INTERNAL_VARIABLE_FIELDS`
//! (`reject_internal_variable_keys` dissolves: an authored internal key is an unknown key).

use crate::board::value::SingleRowBoolProbe;
use crate::facet::DisplayText;
use crate::ids::QueryName;
use crate::query::Cell;
use crate::rule::Auto;
use crate::string_enum;
use model_core::{
    Build, Cx, Engine, FromNode, Leaf, Merge, Model as _, Node, ReadScalar, Scalar, TypeExpr,
};
use model_derive::Model;
use serde_json::Value;

string_enum!(
    /// A concrete control; `auto` is the [`Auto`] rule.
    InputType {
        Select = "select", Multiselect = "multiselect", Input = "input", Text = "text",
        Number = "number", Textarea = "textarea", Slider = "slider", Range = "range",
        Date = "date", Datepicker = "datepicker", Daterange = "daterange",
        Checkbox = "checkbox", Radio = "radio",
    }
);

string_enum!(
    /// The type a select, radio or multiselect sends back.
    VariableDataType {
        String = "string", Number = "number", Date = "date", Boolean = "boolean", Array = "array",
    }
);

/// Option values written out: all strings or all numbers (one type).
#[derive(Debug, Clone, PartialEq)]
pub enum StaticOptions {
    Strings(Vec<String>),
    Numbers(Vec<f64>),
}

impl ReadScalar for StaticOptions {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        let Value::Array(items) = node.to_json() else {
            cx.invalid(
                node.span,
                "options.static must be a list of strings or of numbers",
            );
            return None;
        };
        let bad = |cx: &mut Cx| {
            cx.invalid(
                node.span,
                format!(
                    "options.static must be all strings or all numbers (one type), got {}",
                    diagnostic::py_repr(&Value::Array(items.clone()))
                ),
            );
            None
        };
        if items.iter().all(Value::is_string) {
            let strings = items.iter().filter_map(|v| v.as_str().map(str::to_string));
            return Some(Self::Strings(strings.collect()));
        }
        if items.iter().all(Value::is_number) {
            return Some(Self::Numbers(
                items.iter().filter_map(Value::as_f64).collect(),
            ));
        }
        bad(cx)
    }
}

impl FromNode for StaticOptions {
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

impl Leaf for StaticOptions {
    type Whole = StaticOptions;
    type Authored = Option<StaticOptions>;
    type Patch = Option<StaticOptions>;
    const MERGE: Merge = Merge::Override;
    const EXPR: TypeExpr = TypeExpr::Either(
        &TypeExpr::List(&TypeExpr::Scalar(Scalar::Str)),
        &TypeExpr::List(&TypeExpr::Scalar(Scalar::Float)),
    );
}
model_core::build_leaf!(StaticOptions);

/// Where a select's options come from.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct VariableOptions {
    /// Option values that are not in the data, written out: all strings or all numbers. Numeric options type the value the control sends back as a number unless data_type says otherwise.
    #[model(absent = "options come from the query")]
    pub r#static: Option<StaticOptions>,
    /// Query name whose result rows provide option values.
    #[model(absent = "no option query")]
    pub query: Option<QueryName>,
    /// Column in the query result to use as option values.
    #[model(absent = "no option column")]
    pub column: Option<String>,
    /// Query column shown as the option text, apart from the value.
    #[model(absent = "the value is the text")]
    pub label_column: Option<String>,
}

/// Whether a control is enabled.
#[derive(Debug, Clone, PartialEq)]
pub enum Enabled {
    /// A static `true` or `false`.
    Static(bool),
    /// A variable name or Jinja boolean expression (no `{{ }}` required; bare names auto-wrap).
    Expression(String),
    /// A boolean cell read from a named query.
    Probe(SingleRowBoolProbe),
}

impl ReadScalar for Enabled {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        match node.to_json() {
            Value::Bool(b) => Some(Self::Static(b)),
            Value::String(s) => Some(Self::Expression(s)),
            _ => {
                let probe = <SingleRowBoolProbe as FromNode>::whole(node, cx)?;
                match SingleRowBoolProbe::build_authored(probe, "") {
                    Ok(p) => Some(Self::Probe(p)),
                    Err(unset) => {
                        cx.invalid(node.span, unset.to_string());
                        None
                    }
                }
            }
        }
    }
}

impl FromNode for Enabled {
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

impl Leaf for Enabled {
    type Whole = Enabled;
    type Authored = Option<Enabled>;
    type Patch = Option<Enabled>;
    const MERGE: Merge = Merge::Override;
    const EXPR: TypeExpr = TypeExpr::Either(
        &TypeExpr::Either(
            &TypeExpr::Scalar(Scalar::Bool),
            &TypeExpr::Scalar(Scalar::Str),
        ),
        &TypeExpr::Ref("SingleRowBoolProbe", SingleRowBoolProbe::info),
    );
}
model_core::build_leaf!(Enabled);

crate::style::axis::non_empty_list!(
    /// The picker's preset menu: at least one name.
    Presets,
    String
);

/// Variable definition: a dynamic value for queries and charts, bound to a user input or fixed.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct Variable {
    /// Control type. `auto` (the default) picks one from `options` and `data_type`: omit it except for `daterange`.
    #[model(fill = "auto")]
    pub input: Engine<InputType, Auto>,
    /// Caption naming what this input sets.
    #[model(absent = "no caption")]
    pub label: Option<DisplayText>,
    /// Help text for this input, carried to the host rather than drawn on the board.
    #[model(absent = "no help text")]
    pub notes: Option<DisplayText>,
    /// Value used when no URL param or --var sets one; a dropdown starts at "All", so omit it there. Quote one containing `: `. A daterange takes `[start, end]` or a rolling preset such as `last_90_days`.
    #[model(absent = "no default")]
    pub default: Option<Cell>,
    /// daterange only: the picker's preset menu, in order, replacing `style.variables.presets`. Each is a preset name (`last_30_days`, `previous_3_quarters`, `this_fiscal_quarter`); `default` need not be one.
    #[model(absent = "the menu in style.variables.presets")]
    pub presets: Option<Presets>,
    /// Hint text shown inside the input while it is empty.
    #[model(absent = "no hint text")]
    pub placeholder: Option<String>,
    /// When True, a value must be provided before queries execute.
    #[model(fill = false)]
    pub required: bool,
    /// When False, the variable is not rendered in the UI but can still be set via URL params.
    #[model(fill = true)]
    pub visible: bool,
    /// Enable this control. Accepts: static bool; a variable name or Jinja boolean expression string (no {{ }} required, bare names auto-wrap); or a {query, column} form that reads a single boolean cell from a named query. Absent variable in a string expression raises (use a default).
    #[model(absent = "enabled")]
    pub enabled: Option<Enabled>,
    /// A dropdown of this column's values, as 'table.column'. Use 'schema.table.column' outside the default schema. It starts at "All"; filter() turns "All" into no constraint.
    #[model(absent = "no column binding")]
    pub column: Option<String>,
    /// Query name for populating options.
    #[model(absent = "no option query")]
    pub query: Option<QueryName>,
    /// Dropdown values come from a query column, or (rarely) a written-out list.
    #[model(absent = "no options")]
    pub options: Option<VariableOptions>,
    /// The type of the values a select, radio or multiselect sends back. 'number', 'date' and 'boolean' convert the value before it reaches SQL; needed when the options come from a query, since a static numeric list already implies 'number'. 'string' and 'array' leave the value as sent.
    #[model(absent = "inferred from the options")]
    pub data_type: Option<VariableDataType>,
    /// Minimum value for number, slider and range inputs.
    #[model(absent = "no minimum")]
    pub min: Option<f64>,
    /// Maximum value for number, slider and range inputs.
    #[model(absent = "no maximum")]
    pub max: Option<f64>,
    /// Step size for number, slider and range inputs.
    #[model(absent = "the input's own step")]
    pub step: Option<f64>,
    /// SQL operator used when generating filter expressions (e.g., '=', 'IN', 'LIKE').
    #[model(absent = "chosen from the input type")]
    pub operator: Option<String>,
}
