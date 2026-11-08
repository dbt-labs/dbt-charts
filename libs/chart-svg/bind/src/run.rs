//! What execute hands render: the datasets, variables, chart errors and ambient inputs of one
//! run. Its wire form (serde, version) comes with the host that sends it.

use board_model::ids::{ChartId, QueryId};
use column_types::Dataset;
use diagnostic::Diagnostic;
use indexmap::IndexMap;
use std::collections::BTreeSet;

/// The run of a board's queries under one variable state.
#[derive(Debug, Clone)]
pub struct RunResult {
    /// Each executed query's rows and declared column types. A query from an imported
    /// file has its own id, so same-named queries of two files never collide.
    pub datasets: IndexMap<QueryId, Dataset>,
    /// The resolved variable values, as `plan` returned them.
    pub variables: IndexMap<String, board_model::variable_value::VariableValue>,
    /// The variables the caller set a value for, whatever it equals the default to
    /// (`current_overridden_variables`): the board's reset item is enabled by them. Run input,
    /// keyed like `variables`; the cutover's Python seam must supply it.
    pub overridden: BTreeSet<String>,
    /// The charts whose query failed, each with its execute error: that chart's card.
    pub chart_errors: IndexMap<ChartId, Diagnostic>,
    pub env: RenderEnv,
}

/// The ambient inputs of a render, passed as data: the engine reads no clock or env.
#[derive(Debug, Clone)]
pub struct RenderEnv {
    pub rendered_at: jiff::Timestamp,
    pub locale: String,
    pub time_zone: String,
}
