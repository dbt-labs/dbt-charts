//! A board's title and text with their Jinja filled from the run's variables, as production fills them
//! before it draws (`resolve_display_text`): every layout stage then reads the words that draw. Only a
//! drawn board is filled: production resolves the open tab's pane alone (`render_tabs_layout`) and
//! no hidden item or collapsed section, so their words never refuse the board. A text
//! that reads a query defined in another file is refused by name: the engine fills no data there. The `format`
//! filter reads the board's `style.formats` aliases.

use std::collections::{BTreeMap, BTreeSet};
use std::convert::Infallible;

use bind::run::RunResult;
use bind::bound::DrawnSet;
use board_model::board::layout::{items, Layout, LayoutItem};
use board_model::board::{Board, BoardBody};
use board_model::ids::{BoardId, ItemId};
use bind::board::ChartStyles;
use diagnostic::{refuse, Diagnostic};
use layout::board::prose::has_template_syntax;

use crate::facts::EngineFacts;

/// What sizing reads of a board whose title or text reads query data, which production does not
/// resolve before the render (`text_needs_render_data`): each one's literal words
/// (`template_literal_text`, fenced code kept as written). Production sizes a board by what its author
/// wrote, so a template that fills to nothing still asks for its width.
#[derive(Debug, Default)]
pub(crate) struct ReadsData {
    pub title: Option<String>,
    pub text: Option<String>,
}

/// Each board, by where it sits, whose authored title or text reads query data.
pub(crate) fn reading_data(board: &Board) -> BTreeMap<ItemId, ReadsData> {
    fn walk(body: &BoardBody, at: &ItemId, out: &mut BTreeMap<ItemId, ReadsData>) {
        let literal = |source: &str| -> Option<String> {
            template::reads_render_data(source).then(|| {
                let Ok(literal) = template::mask::resolve_outside_fences(source, |masked| Ok::<_, Infallible>(template::literal_text(masked)));
                literal
            })
        };
        let title = body.title.as_ref().and_then(|title| literal(title.as_str()));
        let text = body.text.as_ref().and_then(|text| literal(text.as_str()));
        if title.is_some() || text.is_some() {
            out.insert(at.clone(), ReadsData { title, text });
        }
        for placement in items(body, at) {
            if let LayoutItem::Board(nested) = placement.item {
                walk(&nested.body, &placement.id, out);
            }
        }
    }
    let mut out = BTreeMap::new();
    walk(&board.body, &ItemId::root(), &mut out);
    out
}

/// `board`, with each title and text that carries template syntax replaced by its filled words.
pub(crate) fn filled(board: &Board, styles: &ChartStyles, run: &RunResult, facts: &impl EngineFacts) -> Result<Board, Diagnostic> {
    let mut shown = BTreeSet::new();
    drawn_boards(&board.body, &ItemId::root(), &bind::board::drawn_set(board, styles, run)?, &mut shown);
    let mut board = board.clone();
    fill(&mut board.body, &shown, styles, run, facts)?;
    Ok(board)
}

/// The id of every board `drawn` draws: `body` unless it is a collapsed section, and the nested
/// boards of its open tab pane that are not hidden.
fn drawn_boards(body: &BoardBody, at: &ItemId, drawn: &DrawnSet, out: &mut BTreeSet<BoardId>) {
    if drawn.details_open(&body.id) == Some(false) {
        return;
    }
    out.insert(body.id.clone());
    let open = drawn.active_tab(&body.id);
    for placement in items(body, at) {
        if open.is_some_and(|open| placement.pane.first() != Some(&open)) || drawn.is_hidden(&placement.id) {
            continue;
        }
        if let LayoutItem::Board(nested) = placement.item {
            drawn_boards(&nested.body, &placement.id, drawn, out);
        }
    }
}

fn fill(body: &mut BoardBody, shown: &BTreeSet<BoardId>, styles: &ChartStyles, run: &RunResult, facts: &impl EngineFacts) -> Result<(), Diagnostic> {
    if !shown.contains(&body.id) {
        return Ok(());
    }
    if let Some(title) = &body.title {
        if has_template_syntax(title.as_str()) {
            body.title = Some(nonempty(words(title.as_str(), body, styles, run, facts)?)?.try_into().map_err(|_| refuse!("a filled board title is not a display text"))?);
        }
    }
    if let Some(text) = &body.text {
        if has_template_syntax(text.as_str()) {
            body.text = Some(nonempty(words(text.as_str(), body, styles, run, facts)?)?.try_into().map_err(|_| refuse!("a filled board text is not markdown"))?);
        }
    }
    fill_layout(&mut body.layout, shown, styles, run, facts)
}

/// Every nested board a layout holds, a tab pane's included; `fill` passes over the ones not drawn.
fn fill_layout(layout: &mut Layout, shown: &BTreeSet<BoardId>, styles: &ChartStyles, run: &RunResult, facts: &impl EngineFacts) -> Result<(), Diagnostic> {
    let items: Vec<&mut LayoutItem> = match layout {
        Layout::Rows(items) | Layout::Cols(items) => items.iter_mut().collect(),
        Layout::Grid(grid) => grid.items.iter_mut().map(|cell| &mut cell.item).collect(),
        Layout::Tabs(tabs) => {
            for tab in &mut tabs.items {
                fill_layout(&mut tab.layout, shown, styles, run, facts)?;
            }
            Vec::new()
        }
    };
    for item in items {
        if let LayoutItem::Board(nested) = item {
            fill(&mut nested.body, shown, styles, run, facts)?;
        }
    }
    Ok(())
}

/// Production draws a title or text by what its author wrote: one that fills to nothing still draws its
/// heading or header, which an empty one, here the same as none, would not.
fn nonempty(filled: String) -> Result<String, Diagnostic> {
    if filled.is_empty() {
        return Err(Diagnostic::refused(refuse!("a board's title or text whose template fills to nothing is not supported")));
    }
    Ok(filled)
}

/// `text` of `body` rendered against the variables and the results of the queries it reads.
fn words(text: &str, body: &BoardBody, styles: &ChartStyles, run: &RunResult, facts: &impl EngineFacts) -> Result<String, Diagnostic> {
    let queries = bind::display_text::text_datasets(text, None, styles, run)?;
    bind::display_text::render_text(text, &run.variables, &queries, &bind::formats::aliases_patch(&facts.style(body)?.formats))
}
