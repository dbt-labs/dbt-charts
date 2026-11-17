//! A callout's text: its message and title as drawn. `chart-renderer/src/families/callout.rs::bind`
//! and `compile/resolve/chart/simple.py::_resolve_callout` decide the same facts. A callout reads no
//! query of its own, so production validates none of its data; its text is filled from the run's
//! variables and the results of the queries it reads (`_resolve_text`), and what this slice does
//! not model is refused by name (a query read from a nested board's text). An image binds as
//! its text writes it: layout draws it from its authored size, as production's fetch-off renderer
//! does.

use board_model::chart::CalloutChart;
use board_model::ids::ChartId;
use diagnostic::Diagnostic;
use indexmap::IndexMap;

use crate::board::ChartStyles;
use crate::bound::{Callout, Failure, Family};
use crate::derived::Placed;
use crate::run::RunResult;

/// Binds `chart`, placed as `placed`, to its filled text.
pub fn bind_callout(id: &ChartId, chart: &CalloutChart, placed: &Placed, styles: &ChartStyles, run: &RunResult) -> Result<Result<Family, Failure>, Diagnostic> {
    let aliases = crate::formats::aliases(&placed.scope().formats);
    let filled = |text: &str| filled_text(id, text, styles, run, &aliases);
    let message = filled(chart.message.as_str())?;
    // `r.title.filter(|t| !t.is_empty())`: an empty title is no title.
    let title = chart.title.as_ref().map(|title| filled(title.as_str())).transpose()?.filter(|title| !title.is_empty());
    Ok(Ok(Family::Callout(Callout { title, message })))
}

/// `text` as `_resolve_text` leaves it: template syntax filled from the run's variables and the
/// queries it reads (`text_datasets`). Its images bind as filled: production's resolve refuses a
/// project image path a template builds (`check_images_written_out`) before the engine draws, and
/// layout draws every other image from the host's images, refusing by name one it was not given.
fn filled_text(id: &ChartId, text: &str, styles: &ChartStyles, run: &RunResult, aliases: &IndexMap<String, crate::formats::Alias>) -> Result<String, Diagnostic> {
    if !(text.contains("{{") || text.contains("{%")) {
        return Ok(text.to_string());
    }
    let queries = crate::display_text::text_datasets(text, Some(id), styles, run)?;
    crate::display_text::render_text(text, &run.variables, &queries, aliases)
}
