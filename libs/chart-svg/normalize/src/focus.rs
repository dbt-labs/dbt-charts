//! `chart_focus` (Python's `focus_on_chart`, normalize's step 9): the board narrows to the one
//! chart it names, drawn alone at its own width, with only the variables that chart depends on.
//! Charts and queries stay as they are, since names are board-global.
//!
//! The layout facts are read again after narrowing, so every fact and style is keyed by the
//! position the chart now holds.

use crate::compile::CompileError;
use crate::deps::Dependencies;
use crate::index::BoardIndex;
use board_model::board::layout::{LayoutAuthored, LayoutItemAuthored};
use diagnostic::diag;
use model_core::Tri;

type Patch = <board_model::board::Board as model_core::Leaf>::Patch;

/// `patch` narrowed to its `chart_focus`, when it names one; whether it was.
///
/// The chart is named as users see it (`index.public_ids`), as Python's `board.charts` is keyed.
/// What Python resolves against the board or file that declares a chart is ported for the root
/// only, so a chart declared below it or imported is refused by name.
pub fn focus(
    patch: &mut Patch,
    index: &BoardIndex,
    all: &Dependencies,
) -> Result<bool, CompileError> {
    let Tri::Value(named) = &patch.chart_focus else {
        return Ok(false);
    };
    let found = index
        .public_ids
        .iter()
        .find(|(_, public)| public.as_str() == named.as_str())
        .map(|(key, _)| key.clone());
    let key = match found {
        Some(key) if matches!(patch.body.charts.get(key.as_str()), Some(Some(_))) => key,
        _ => {
            let mut available: Vec<&str> = index.public_ids.values().map(String::as_str).collect();
            available.sort_unstable();
            return Err(CompileError::Invalid(vec![diag::err_unknown_chart(
                named.as_str(),
                available,
            )]));
        }
    };
    if index.declared_below.contains(key.as_str()) {
        return Err(CompileError::internal(
            "chart_focus on a chart a nested board or another file declares",
        ));
    }
    let needed = all
        .charts
        .get(key.as_str())
        .ok_or_else(|| CompileError::internal("a focused chart has no dependencies"))?;
    if needed.iter().any(|n| index.variable_boards.contains_key(n)) {
        return Err(CompileError::internal(
            "chart_focus on a chart that reads a variable a nested board declares",
        ));
    }
    patch.body.variables.retain(|name, _| needed.contains(name));
    patch.body.layout = Some(LayoutAuthored::Rows(vec![LayoutItemAuthored::Chart(key)]));
    patch.body.title = Tri::Unset;
    patch.width = Tri::Unset;
    Ok(true)
}
