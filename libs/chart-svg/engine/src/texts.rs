//! A board's title and text with their Jinja filled from the run's variables, as production fills them
//! before it draws (`resolve_display_text`): every layout stage then reads the words that draw. A text
//! that reads a query defined in another file is refused by name: the engine fills no data there. The `format`
//! filter reads the board's `style.formats` aliases.

use bind::run::RunResult;
use board_model::board::layout::{Layout, LayoutItem};
use board_model::board::{Board, BoardBody};
use bind::board::ChartStyles;
use diagnostic::{diag, refuse, Diagnostic};
use indexmap::IndexMap;
use layout::board::prose::has_template_syntax;
use template::environment::Kind;

use crate::facts::EngineFacts;

/// `board`, with each title and text that carries template syntax replaced by its filled words.
pub(crate) fn filled(board: &Board, styles: &ChartStyles, run: &RunResult, facts: &impl EngineFacts) -> Result<Board, Diagnostic> {
    let mut board = board.clone();
    fill(&mut board.body, styles, run, facts)?;
    Ok(board)
}

fn fill(body: &mut BoardBody, styles: &ChartStyles, run: &RunResult, facts: &impl EngineFacts) -> Result<(), Diagnostic> {
    if let Some(title) = &body.title {
        if has_template_syntax(title.as_str()) {
            body.title = Some(words(title.as_str(), body, styles, run, facts)?.try_into().map_err(|_| refuse!("a filled board title is not a display text"))?);
        }
    }
    if let Some(text) = &body.text {
        if has_template_syntax(text.as_str()) {
            body.text = Some(words(text.as_str(), body, styles, run, facts)?.try_into().map_err(|_| refuse!("a filled board text is not markdown"))?);
        }
    }
    let items: Vec<&mut LayoutItem> = match &mut body.layout {
        Layout::Rows(items) | Layout::Cols(items) => items.iter_mut().collect(),
        Layout::Grid(grid) => grid.items.iter_mut().map(|cell| &mut cell.item).collect(),
        Layout::Tabs(_) => Vec::new(),
    };
    for item in items {
        if let LayoutItem::Board(nested) = item {
            fill(&mut nested.body, styles, run, facts)?;
        }
    }
    Ok(())
}

/// `text` of `body` rendered against the variables and the results of the queries it reads.
fn words(text: &str, body: &BoardBody, styles: &ChartStyles, run: &RunResult, facts: &impl EngineFacts) -> Result<String, Diagnostic> {
    let mut queries = IndexMap::new();
    if let Ok(info) = template::parse(text, Kind::Text) {
        if info.queries_nonliteral {
            return Err(Diagnostic::refused(refuse!("a board's title or text that reads a query by a computed name is not supported")));
        }
        for name in &info.queries {
            let Some(id) = styles.root_queries.get(name) else {
                return Err(Diagnostic::refused(refuse!("a board's title or text that reads a query the board does not define is not supported")));
            };
            let dataset = run.datasets.get(id).ok_or_else(|| diag::err_engine_needs_data(id.to_string()))?;
            queries.insert(name.clone(), dataset.clone());
        }
    }
    bind::display_text::render_text(text, &run.variables, &queries, &bind::formats::aliases_patch(&facts.style(body)?.formats))
}
