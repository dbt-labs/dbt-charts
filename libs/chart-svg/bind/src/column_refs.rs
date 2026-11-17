//! A chart's column references and the columns its query returned: each reference takes the result's
//! own spelling (`dbt_charts/core/compile/column_match.py`'s `match_column`), and a chart naming none
//! of its channels binds the result's columns by position (`resolve/chart/positional.py`'s
//! `bind_channels`). Both run on the bound row's columns, in query order.

use diagnostic::{diag, py_str_repr, Diagnostic};
use unicase::UniCase;

/// The result column `reference` names: its exact spelling, else the one column equal to it under
/// Unicode full case folding (Python's `str.casefold`; `unicase`'s tables, whose Unicode version may
/// trail CPython's for characters assigned since); `None` when none does. Two columns that fold
/// alike are `ERR-COLUMN-REF-AMBIGUOUS`, `at` naming the reference.
pub fn match_column<'a>(reference: &str, columns: &[&'a str], at: &str) -> Result<Option<&'a str>, Diagnostic> {
    if let Some(exact) = columns.iter().find(|column| **column == reference) {
        return Ok(Some(exact));
    }
    let folded = UniCase::new(reference);
    let matches: Vec<&str> = columns.iter().copied().filter(|column| UniCase::new(*column) == folded).collect();
    match matches.as_slice() {
        [] => Ok(None),
        [column] => Ok(Some(column)),
        many => Err(diag::err_column_ref_ambiguous(at, reference, many.iter().map(|m| py_str_repr(m)).collect::<Vec<_>>().join(", "))),
    }
}

/// `reference` in the result's spelling; one naming no column stays as authored, so its own
/// not-found error reports it. A result with no columns (zero rows) leaves every reference as is.
pub fn renamed(reference: &str, columns: &[&str], at: &str) -> Result<String, Diagnostic> {
    Ok(match_column(reference, columns, at)?.unwrap_or(reference).to_string())
}

/// The channels a chart naming none of them binds from `columns`: the shape among `shapes` (a column
/// count and the channels it binds, in order) whose count the result has. `None` when the result has
/// no columns to bind; a count no shape takes is `ERR-CHART-CHANNELS-UNBOUND`.
pub fn by_position<'a>(chart: &str, family: &str, shapes: &[(usize, &[&'static str])], columns: &[&'a str]) -> Result<Option<Vec<(&'static str, &'a str)>>, Diagnostic> {
    if columns.is_empty() {
        return Ok(None);
    }
    if let Some((_, order)) = shapes.iter().find(|(count, _)| *count == columns.len()) {
        return Ok(Some(order.iter().copied().zip(columns.iter().copied()).collect()));
    }
    let mut channels: Vec<&str> = Vec::new();
    for channel in shapes.iter().flat_map(|(_, order)| order.iter().copied()) {
        if !channels.contains(&channel) {
            channels.push(channel);
        }
    }
    let shapes = shapes
        .iter()
        .map(|(count, order)| format!("{count} column{} {}", if *count == 1 { " binds" } else { "s bind" }, order.join(", ")))
        .collect::<Vec<_>>()
        .join("; ");
    let plural = if columns.len() == 1 { "" } else { "s" };
    let outcome = format!("Query returned {} column{plural}: {}.", columns.len(), columns.join(", "));
    Err(diag::err_chart_channels_unbound(chart, family, names(&channels, "or"), shapes, outcome))
}

/// `_names`: each channel in backticks, the last joined by `joiner`.
fn names(channels: &[&str], joiner: &str) -> String {
    let quoted: Vec<String> = channels.iter().map(|channel| format!("`{channel}`")).collect();
    match quoted.as_slice() {
        [] => String::new(),
        [one] => one.clone(),
        [init @ .., last] => format!("{} {joiner} {last}", init.join(", ")),
    }
}
