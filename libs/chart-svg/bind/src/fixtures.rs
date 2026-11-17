//! The run a test builds: `run_result` is the one place a test constructs a [`RunResult`], so a field
//! added to it touches this file and no test. A test that exercises another field sets it on the result.

use board_model::ids::QueryId;
use column_types::Dataset;
use indexmap::IndexMap;

use crate::run::{RenderEnv, RunResult};

/// The instant the recorded production twins render at.
pub const RENDERED_AT: &str = "2026-11-06T12:00:00Z";

/// A run of `datasets` and nothing else: no variables, chart errors, empty filters or geo sources, and no link
/// context, rendered at [`RENDERED_AT`].
pub fn run_result(datasets: IndexMap<QueryId, Dataset>) -> RunResult {
    RunResult {
        datasets,
        variables: IndexMap::new(),
        overridden: Default::default(),
        presets: Default::default(),
        chart_errors: IndexMap::new(),
        empty_filters: IndexMap::new(),
        automatic_links: IndexMap::new(),
        geo: IndexMap::new(),
        env: RenderEnv { link_context: None, rendered_at: RENDERED_AT.parse().expect("RENDERED_AT is an RFC 3339 instant") },
    }
}
