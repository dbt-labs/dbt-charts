//! A callout's text: its message and title as drawn. `chart-renderer/src/families/callout.rs::bind`
//! and `compile/resolve/chart/simple.py::_resolve_callout` decide the same facts; a callout reads no
//! query, so production validates none of its data, and what this slice does not model is refused by
//! name (a template or an image).

use board_model::chart::CalloutChart;
use diagnostic::{refuse, Diagnostic};

use crate::bound::{Failure, Family, Callout};

/// Binds `chart` to its authored text.
pub fn bind_callout(chart: &CalloutChart) -> Result<Result<Family, Failure>, Diagnostic> {
    let message = text(chart.message.as_str(), "message")?;
    // `r.title.filter(|t| !t.is_empty())`: an empty title is no title.
    let title = chart.title.as_ref().map(|title| title.as_str()).filter(|title| !title.is_empty());
    let title = title.map(|title| text(title, "title")).transpose()?;
    Ok(Ok(Family::Callout(Callout { title, message })))
}

/// The text as `_resolve_text` leaves it: Jinja needs the board's variables and queries, and an
/// image needs the project's files.
fn text(text: &str, field: &str) -> Result<String, Diagnostic> {
    if text.contains("{{") || text.contains("{%") {
        return Err(Diagnostic::refused(refuse!("a callout {field} with template syntax is not supported")));
    }
    if text.contains("![") {
        return Err(Diagnostic::refused(refuse!("a callout {field} with an image is not supported")));
    }
    Ok(text.to_string())
}
