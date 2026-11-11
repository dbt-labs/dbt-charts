//! A layout item that names a board file (`rows: [b.yml]`) becomes the nested board that file
//! holds, as Python's `_resolve_board_file_import` places it: the file is read anchored at the
//! placing board's directory, its own refs anchor at its own directory, and what it declares
//! (charts, queries, source, variables) is then an ordinary nested board's, so hoisting, ownership
//! and tree-position ids apply to it unchanged. A board file that is a cycle, or is missing, is
//! refused by Python's code. A nested board's `extends:` is inert, as in Python: it is not folded
//! here.
//!
//! A placement this pass does not read (a templated path, a markdown board, a file that does not
//! parse or holds no content) is left a file name, and the layout facts refuse it by name.

use crate::external::{dir_of, External, Imports};
use crate::hoist::HoistError;
use crate::index::is_board_file;
use board_model::board::layout::{LayoutAuthored, LayoutItemAuthored, NestedBoardAuthored};
use board_model::board::{Board, BoardBodyAuthored};
use board_model::ids::ItemId;
use diagnostic::diag;
use model_core::{parse, Tri};
use project::ProjectPath;

/// Replaces every board-file placement of `layout`, at any depth, with the board it names. `at` is
/// the tree position of the board that holds `layout` and `chain` the files being inlined, the
/// root first.
pub(crate) fn inline(
    layout: &mut LayoutAuthored,
    at: &ItemId,
    chain: &mut Vec<ProjectPath>,
    external: &External,
    imports: &mut Imports,
) -> Result<(), HoistError> {
    let mut each = |pos: ItemId, item: &mut LayoutItemAuthored| match item {
        LayoutItemAuthored::Chart(name) if is_board_file(name.as_str()) => {
            if let Some(nested) = read(name.as_str(), &pos, chain, external, imports)? {
                if crate::board_warnings::fragmenting_body(&nested.body) {
                    imports.prose.insert(pos);
                }
                *item = LayoutItemAuthored::Board(nested);
            }
            Ok(())
        }
        LayoutItemAuthored::Board(nested) => match &mut nested.body.layout {
            Some(l) => inline(l, &pos, chain, external, imports),
            None => Ok(()),
        },
        _ => Ok(()),
    };
    match layout {
        LayoutAuthored::Rows(items) => items
            .iter_mut()
            .enumerate()
            .try_for_each(|(i, it)| each(at.key("rows").index(i), it)),
        LayoutAuthored::Cols(items) => items
            .iter_mut()
            .enumerate()
            .try_for_each(|(i, it)| each(at.key("cols").index(i), it)),
        LayoutAuthored::Grid(g) => g
            .items
            .iter_mut()
            .flatten()
            .enumerate()
            .filter_map(|(i, cell)| Some((i, cell.item.as_mut()?)))
            .try_for_each(|(i, it)| each(at.key("grid").key("items").index(i).key("item"), it)),
        LayoutAuthored::Tabs(t) => t
            .items
            .iter_mut()
            .flatten()
            .enumerate()
            .filter_map(|(i, tab)| Some((i, tab.layout.as_mut()?)))
            .try_for_each(|(i, l)| {
                let pane = at.key("tabs").key("items").index(i);
                inline(l, &pane, chain, external, imports)
            }),
    }
}

/// The board `name` holds, placed from the file at the end of `chain`; `None` for a placement this
/// pass leaves to the layout facts.
fn read(
    name: &str,
    at: &ItemId,
    chain: &mut Vec<ProjectPath>,
    external: &External,
    imports: &mut Imports,
) -> Result<Option<NestedBoardAuthored>, HoistError> {
    if name.contains("{{") || name.contains("{%") || name.ends_with(".md") {
        return Ok(None);
    }
    let Some(placer) = chain.last() else {
        return Ok(None);
    };
    let dir = dir_of(placer).to_string();
    let root_dir = chain.first().map_or("", dir_of).to_string();
    let Ok(path) = ProjectPath::anchored(&dir, name) else {
        return Ok(None);
    };
    if let Some(at) = chain.iter().position(|p| *p == path) {
        let cycle: Vec<&str> = chain
            .iter()
            .skip(at)
            .chain([&path])
            .map(ProjectPath::as_str)
            .collect();
        return Err(HoistError::Cycle(Box::new(diag::err_board_import_cycle(
            cycle.join(" -> "),
        ))));
    }
    let (path, mut node) = external.open(name, &dir, name)?;
    if crate::shape::refuse(&node, false).is_err() || crate::names::refuse(&node, false).is_err() {
        return Ok(None);
    }
    external.resolve_refs(&mut node, &path, imports)?;
    let parsed = parse::<Board>(&node, None);
    let mut body = parsed.twin.body;
    if !parsed.errors.is_empty() || !crate::hoist::has_content!(body) {
        return Ok(None);
    }
    if !matches!(body.details, Tri::Unset) {
        return Err(HoistError::NotYet(format!(
            "{path}: an imported board that sets details: is not ported yet"
        )));
    }
    if dir_of(&path) != root_dir && declares(&body) {
        return Err(HoistError::NotYet(format!(
            "{path}: an imported board in another directory that declares charts, queries or a \
             source is not ported yet"
        )));
    }
    chain.push(path);
    let inlined = match &mut body.layout {
        Some(l) => inline(l, at, chain, external, imports),
        None => Ok(()),
    };
    chain.pop();
    inlined?;
    imports.boards = true;
    Ok(Some(NestedBoardAuthored {
        body,
        ..NestedBoardAuthored::default()
    }))
}

/// Whether a board, or a board written inline in its layout, holds anything whose relative paths
/// anchor at its own directory.
fn declares(body: &BoardBodyAuthored) -> bool {
    !body.charts.is_empty()
        || !body.queries.is_empty()
        || !matches!(body.source, Tri::Unset)
        || body.layout.as_ref().is_some_and(layout_declares)
}

fn layout_declares(layout: &LayoutAuthored) -> bool {
    let nested = |item: &LayoutItemAuthored| matches!(item, LayoutItemAuthored::Board(n) if declares(&n.body));
    match layout {
        LayoutAuthored::Rows(items) | LayoutAuthored::Cols(items) => items.iter().any(nested),
        LayoutAuthored::Grid(g) => g
            .items
            .iter()
            .flatten()
            .filter_map(|c| c.item.as_ref())
            .any(nested),
        LayoutAuthored::Tabs(t) => t
            .items
            .iter()
            .flatten()
            .filter_map(|tab| tab.layout.as_ref())
            .any(layout_declares),
    }
}
