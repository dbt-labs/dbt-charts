//! What execute hands render: the datasets, variables, chart errors and ambient inputs of one
//! run. Its wire form (serde, version) comes with the host that sends it.

use board_model::ids::{ChartId, QueryId};
use column_types::Dataset;
use diagnostic::{diag, Diagnostic};
use serde_json::json;
use indexmap::IndexMap;
use std::collections::BTreeSet;

/// The run of a board's queries under one variable state.
#[derive(Debug, Clone)]
pub struct RunResult {
    /// Each executed query's rows and declared column types. A query from an imported
    /// file has its own id, so same-named queries of two files never collide.
    pub datasets: IndexMap<QueryId, Dataset>,
    /// The resolved variable values, as `plan` returned them: the board's visible variables and the
    /// hidden ones normalize generates for tabs and details, each at its default unless set, in
    /// declaration order (a toggle link carries every one).
    pub variables: IndexMap<String, board_model::variable_value::VariableValue>,
    /// The variables the caller set a value for, whatever it equals the default to
    /// (`current_overridden_variables`): the board's reset item is enabled by them. Run input,
    /// keyed like `variables`; the cutover's Python seam must supply it.
    pub overridden: BTreeSet<String>,
    /// The charts whose query failed, each with its execute error: that chart's card.
    pub chart_errors: IndexMap<ChartId, Diagnostic>,
    pub env: RenderEnv,
}

impl RunResult {
    /// The result of `query` for the chart `id` reading it; a run without one needs the data
    /// before the chart can bind.
    pub fn dataset(&self, query: &QueryId, id: &ChartId) -> Result<&Dataset, Diagnostic> {
        self.datasets
            .get(query)
            .ok_or_else(|| diag::err_engine_needs_data(query.to_string()).with_sink_param("chart_id", json!(id.as_str())))
    }
}

/// The ambient inputs of a render, passed as data: the engine reads no clock or env. What the board states
/// (its locales, language and time zone) is read from the board, never from here.
#[derive(Debug, Clone)]
pub struct RenderEnv {
    pub rendered_at: jiff::Timestamp,
}
