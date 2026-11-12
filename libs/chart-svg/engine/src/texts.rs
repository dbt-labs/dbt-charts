//! A board's title and text with their Jinja filled from the run's variables, as production fills them
//! before it draws (`resolve_display_text`): every layout stage then reads the words that draw. A text
//! that reads a query result, or a board that redefines `style.formats`, is refused by name: the engine
//! fills no data and passes the filter no board formats yet.

use bind::run::RunResult;
use board_model::board::layout::{Layout, LayoutItem};
use board_model::board::{Board, BoardBody};
use diagnostic::{refuse, Diagnostic};
use indexmap::IndexMap;
use layout::board::prose::has_template_syntax;
use template::environment::Kind;

use crate::facts::EngineFacts;

/// `board`, with each title and text that carries template syntax replaced by its filled words.
pub(crate) fn filled(board: &Board, run: &RunResult, facts: &impl EngineFacts) -> Result<Board, Diagnostic> {
    let mut board = board.clone();
    fill(&mut board.body, run, facts)?;
    Ok(board)
}

fn fill(body: &mut BoardBody, run: &RunResult, facts: &impl EngineFacts) -> Result<(), Diagnostic> {
    if let Some(title) = &body.title {
        if has_template_syntax(title.as_str()) {
            body.title = Some(words(title.as_str(), body, run, facts)?.try_into().map_err(|_| refuse!("a filled board title is not a display text"))?);
        }
    }
    if let Some(text) = &body.text {
        if has_template_syntax(text.as_str()) {
            body.text = Some(words(text.as_str(), body, run, facts)?.try_into().map_err(|_| refuse!("a filled board text is not markdown"))?);
        }
    }
    let items: Vec<&mut LayoutItem> = match &mut body.layout {
        Layout::Rows(items) | Layout::Cols(items) => items.iter_mut().collect(),
        Layout::Grid(grid) => grid.items.iter_mut().map(|cell| &mut cell.item).collect(),
        Layout::Tabs(_) => Vec::new(),
    };
    for item in items {
        if let LayoutItem::Board(nested) = item {
            fill(&mut nested.body, run, facts)?;
        }
    }
    Ok(())
}

/// `text` of `body` rendered against the variables alone.
fn words(text: &str, body: &BoardBody, run: &RunResult, facts: &impl EngineFacts) -> Result<String, Diagnostic> {
    if !facts.style(body)?.formats.is_empty() {
        return Err(Diagnostic::refused(refuse!("a board's `style.formats` is not supported")));
    }
    if let Ok(info) = template::parse(text, Kind::Text) {
        if !info.queries.is_empty() || info.queries_nonliteral {
            return Err(Diagnostic::refused(refuse!("a board's title or text that reads a query result is not supported")));
        }
    }
    bind::display_text::render_text(text, &run.variables, &IndexMap::new(), &IndexMap::new())
}
