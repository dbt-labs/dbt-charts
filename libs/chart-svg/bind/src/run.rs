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
    /// For each chart, by its public id, that can draw empty, the filters that reach it and hold a value: the variable bar's label and the
    /// text it displays, as the host resolved them. A chart with no entry names none.
    pub empty_filters: IndexMap<String, Vec<(String, String)>>,
    /// For each chart, by its public id, the link production resolved for it at run time (an
    /// authored `link:`, or the one the project's data source synthesizes when the chart authors none,
    /// `automatic_link.py`), as its template. A chart with no entry has none. The engine reads it only
    /// for a chart that authors no `link:` (`links::chart_link`). Run input: the compiled board cannot
    /// know the synthesized one.
    pub automatic_links: IndexMap<String, String>,
    /// The geo sources the host resolved, by the source a chart names: its descriptor and document
    /// text. Empty for a board that draws no map.
    pub geo: IndexMap<String, crate::geo_source::GeoInput>,
    pub env: RenderEnv,
}

impl RunResult {
    /// The filters that reach chart `public_id` and hold a value, as `Label: value` joined by ` · `
    /// (`no_results::context_line`); `None` when it names none.
    pub fn empty_filters_line(&self, public_id: &str) -> Option<String> {
        let filters = self.empty_filters.get(public_id).filter(|filters| !filters.is_empty())?;
        let named: Vec<String> = filters.iter().map(|(label, text)| format!("{label}: {text}")).collect();
        Some(named.join(" \u{b7} "))
    }

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
