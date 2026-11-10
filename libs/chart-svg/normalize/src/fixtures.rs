//! A board with its layout facts, for tests that need both before normalize can produce them
//! (cargo feature `fixtures`). The board is `board_model::fixtures`'; the facts are keyed by that
//! board's own ids, and name variables as Python does for the same tree.

// Test support behind a feature: a literal that fails to build is a bug in the fixture itself.
#![allow(clippy::expect_used)]

use crate::compile::HostInputs;
use crate::index::{DetailsFacts, LayoutFacts, TabsFacts};
use crate::slug::slugify;
use crate::sources::SourceRegistry;
use board_model::board::{Board, BoardBody, Layout, LayoutItem};
use board_model::fixtures as boards;
use board_model::ids::{BoardId, ItemId};

fn id(name: &str) -> BoardId {
    BoardId::try_from(name).expect("fixture board ids are not empty")
}

fn tabs_facts(variable: &str, titles: &[&str]) -> TabsFacts {
    TabsFacts {
        variable: variable.to_string(),
        slugs: titles.iter().map(|t| slugify(t)).collect(),
        default_tab: 0,
    }
}

/// Give every board body the id its tree position names, as the facts are keyed.
fn position_ids(body: &mut BoardBody, at: &ItemId) {
    body.id = id(at.as_str());
    layout_ids(&mut body.layout, at);
}

fn layout_ids(layout: &mut Layout, at: &ItemId) {
    let items = |items: &mut Vec<LayoutItem>, base: ItemId| {
        for (i, item) in items.iter_mut().enumerate() {
            if let LayoutItem::Board(b) = item {
                position_ids(&mut b.body, &base.index(i));
            }
        }
    };
    match layout {
        Layout::Rows(v) => items(v, at.key("rows")),
        Layout::Cols(v) => items(v, at.key("cols")),
        Layout::Grid(g) => {
            let base = at.key("grid").key("items");
            for (i, cell) in g.items.iter_mut().enumerate() {
                if let LayoutItem::Board(b) = &mut cell.item {
                    position_ids(&mut b.body, &base.index(i).key("item"));
                }
            }
        }
        Layout::Tabs(t) => {
            let base = at.key("tabs").key("items");
            for (i, tab) in t.items.iter_mut().enumerate() {
                layout_ids(&mut tab.layout, &base.index(i));
            }
        }
    }
}

fn positioned(mut board: Board) -> Board {
    position_ids(&mut board.body, &ItemId::root());
    board
}

/// `board_model::fixtures::tabs`, its first tab open. Every body's id is its tree position, the
/// key the facts use; the root is `@`.
pub fn tabs(tabs: &[(&str, &[&str])]) -> (Board, LayoutFacts) {
    let titles: Vec<&str> = tabs.iter().map(|(t, _)| *t).collect();
    let mut facts = LayoutFacts::default();
    facts
        .tabs
        .insert(ItemId::root(), tabs_facts("_tab_tabs", &titles));
    (positioned(boards::tabs(tabs)), facts)
}

/// `board_model::fixtures::layout_tree`: its nested tabs board is `@rows[3]`, the fourth item of
/// the root's rows, so Python names the variable `_tab_tree_row3`.
pub fn layout_tree() -> (Board, LayoutFacts) {
    let mut facts = LayoutFacts::default();
    facts.tabs.insert(
        ItemId::root().key("rows").index(3),
        tabs_facts("_tab_tree_row3", &["One", "Two"]),
    );
    (positioned(boards::layout_tree()), facts)
}

/// `board_model::fixtures::nested`, its nested board (`@rows[1]`) collapsible, so Python names
/// the variable `_details_row1`.
pub fn nested_details(expanded: bool) -> (Board, LayoutFacts) {
    let mut facts = LayoutFacts::default();
    facts.details.insert(
        ItemId::root().key("rows").index(1),
        DetailsFacts {
            variable: "_details_row1".to_string(),
            expanded,
        },
    );
    (positioned(boards::nested()), facts)
}

static NO_SOURCES: SourceRegistry = SourceRegistry::empty();

/// The host inputs the tests share: the project root, a fixed day, the `clarity` theme, no pinned
/// ceiling, opaque SQL and no declared sources. A test varies one field by struct update.
pub fn host_inputs() -> HostInputs<'static> {
    HostInputs {
        boards_root: ".",
        today: (2026, 11, 6),
        default_theme: "clarity",
        html_policy_ceiling: None,
        sql: &boards::Opaque,
        sources: &NO_SOURCES,
    }
}
