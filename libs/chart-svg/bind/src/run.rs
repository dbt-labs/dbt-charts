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
    /// The daterange variables whose value is a preset, by name, to the preset id (`plan`'s
    /// `variables.presets`): what a daterange control publishes and links by. A name with no
    /// entry holds fixed dates. Run input, keyed like `variables`.
    pub presets: IndexMap<String, String>,
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

/// One query's `{rows, schema}` from the host's wire: the one place the wire's cell markers
/// (`rows::decode_cells`) are decoded, so every family reads plain numbers and `cell_text` keeps
/// the text the markers carried.
pub fn dataset_of(wire: serde_json::Value) -> Result<Dataset, String> {
    let mut dataset: Dataset = serde_json::from_value(wire).map_err(|e| e.to_string())?;
    dataset.cell_text = crate::rows::decode_cells(&mut dataset.rows)?;
    Ok(dataset)
}

/// The ambient inputs of a render, passed as data: the engine reads no clock or env. What the board states
/// (its locales, language and time zone) is read from the board, never from here.
#[derive(Debug, Clone)]
pub struct RenderEnv {
    pub rendered_at: jiff::Timestamp,
    /// Where the host serves boards. A board that links needs one: without it that link is an error.
    pub link_context: Option<crate::links::LinkContext>,
}
