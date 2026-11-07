//! Boards for tests that need a [`Board`] before normalize exists (cargo feature `fixtures`).
//!
//! Each is built from a patch through `Board::try_from`, the call normalize's construct pass
//! makes, so a fixture cannot hold a state normalize would not produce. The values `fill`
//! supplies (ids, `html_policy`, `card_gap`, empty `tags`) are set as that pass sets them.
//! A layout names its charts by [`ChartId`] and the maps start empty, so a name has no chart
//! behind it until the test inserts one (`board.charts.insert(name, fixtures::callout_chart(..)?)`).
//!
//! | Builder | Board |
//! |---|---|
//! | [`text_only`] | a titled board with a text block and an empty layout |
//! | [`rows`], [`cols`] | the charts stacked, or side by side |
//! | [`grid`] | the charts at given cells of a grid |
//! | [`tabs`] | one tab per `(title, charts)` |
//! | [`nested`] | a chart above a half-width nested board of two charts |
//! | [`layout_tree`] | rows holding a chart and a nested board of each other kind |

// Test support behind a feature: a literal that fails to parse is a bug in the fixture itself.
#![allow(clippy::expect_used)]

use crate::board::layout::{
    GridItemAuthored, GridLayoutAuthored, LayoutAuthored, LayoutItemAuthored, NestedBoardAuthored,
    TabItemAuthored, TabLayoutAuthored,
};
use crate::board::{Board, BoardBodyAuthored, BoardBodyPatch, BoardPatch, HtmlPolicy, Length};
use crate::facet::{DisplayText, Markdown};
use crate::ids::{BoardId, ChartId};
use model_core::Tri;

fn id(name: &str) -> BoardId {
    BoardId::try_from(name).expect("fixture board ids are not empty")
}

fn chart(name: &str) -> ChartId {
    ChartId::try_from(name).expect("fixture chart names are authored names")
}

fn text(s: &str) -> DisplayText {
    DisplayText::try_from(s).expect("display text has no shape")
}

fn items(charts: &[&str]) -> Vec<LayoutItemAuthored> {
    charts
        .iter()
        .map(|c| LayoutItemAuthored::Chart(chart(c)))
        .collect()
}

fn board(board_id: &str, layout: LayoutAuthored) -> BoardPatch {
    BoardPatch {
        tags: Some(Vec::new()),
        aliases: Some(Vec::new()),
        body: BoardBodyPatch {
            id: Some(id(board_id)),
            html_policy: Some(HtmlPolicy::Escaped),
            card_gap: Some(false),
            layout: Some(layout),
            ..BoardBodyPatch::default()
        },
        ..BoardPatch::default()
    }
}

fn built(patch: BoardPatch) -> Board {
    Board::try_from(patch).expect("a fixture sets every field normalize fill would")
}

fn nested_board(
    board_id: &str,
    width: Option<Length>,
    layout: LayoutAuthored,
) -> LayoutItemAuthored {
    LayoutItemAuthored::Board(NestedBoardAuthored {
        width: width.map_or(Tri::Unset, Tri::Value),
        body: BoardBodyAuthored {
            id: Some(id(board_id)),
            html_policy: Some(HtmlPolicy::Escaped),
            card_gap: Some(false),
            layout: Some(layout),
            ..BoardBodyAuthored::default()
        },
        ..NestedBoardAuthored::default()
    })
}

fn tab(title: &str, charts: &[&str]) -> TabItemAuthored {
    TabItemAuthored {
        title: Some(text(title)),
        layout: Some(LayoutAuthored::Rows(items(charts))),
        ..TabItemAuthored::default()
    }
}

fn grid_layout(columns: u32, cells: &[(&str, i64, i64, u32, u32)]) -> LayoutAuthored {
    LayoutAuthored::Grid(GridLayoutAuthored {
        columns: Some(columns),
        items: Some(
            cells
                .iter()
                .map(|&(name, col, row, col_span, row_span)| GridItemAuthored {
                    item: Some(LayoutItemAuthored::Chart(chart(name))),
                    col: Tri::Value(col),
                    row: Tri::Value(row),
                    col_span: Tri::Value(col_span),
                    row_span: Tri::Value(row_span),
                    ..GridItemAuthored::default()
                })
                .collect(),
        ),
    })
}

fn tabs_layout(tabs: &[(&str, &[&str])]) -> LayoutAuthored {
    LayoutAuthored::Tabs(TabLayoutAuthored {
        position: Some(crate::board::TabPosition::Top),
        items: Some(tabs.iter().map(|(t, c)| tab(t, c)).collect()),
        ..TabLayoutAuthored::default()
    })
}

/// A titled board with one text block and no charts: the layout is empty rows.
pub fn text_only(title: &str, body: &str) -> Board {
    let mut patch = board("text-only", LayoutAuthored::Rows(Vec::new()));
    patch.body.title = Tri::Value(text(title));
    patch.body.text = Tri::Value(Markdown::try_from(body).expect("markdown has no shape"));
    built(patch)
}

/// The named charts stacked vertically.
pub fn rows(charts: &[&str]) -> Board {
    built(board("rows", LayoutAuthored::Rows(items(charts))))
}

/// The named charts side by side.
pub fn cols(charts: &[&str]) -> Board {
    built(board("cols", LayoutAuthored::Cols(items(charts))))
}

/// A `columns`-wide grid; each cell is `(chart, col, row, col_span, row_span)`.
pub fn grid(columns: u32, cells: &[(&str, i64, i64, u32, u32)]) -> Board {
    built(board("grid", grid_layout(columns, cells)))
}

/// Tabs, one per `(title, charts)`, the bar on top; each tab stacks its charts.
pub fn tabs(tabs: &[(&str, &[&str])]) -> Board {
    built(board("tabs", tabs_layout(tabs)))
}

/// `a` above a nested board of `b` and `c` side by side, half the parent's width.
pub fn nested() -> Board {
    let inner = nested_board(
        "nested.cols",
        Some(Length::Percent(50.0)),
        LayoutAuthored::Cols(items(&["b", "c"])),
    );
    let mut stack = items(&["a"]);
    stack.push(inner);
    built(board("nested", LayoutAuthored::Rows(stack)))
}

/// Rows holding chart `a` and one nested board of each other layout kind: cols, grid, tabs.
pub fn layout_tree() -> Board {
    let mut stack = items(&["a"]);
    stack.push(nested_board(
        "tree.cols",
        None,
        LayoutAuthored::Cols(items(&["b", "c"])),
    ));
    stack.push(nested_board(
        "tree.grid",
        None,
        grid_layout(12, &[("d", 0, 0, 6, 1), ("e", 6, 0, 6, 1)]),
    ));
    stack.push(nested_board(
        "tree.tabs",
        None,
        tabs_layout(&[("One", &["f"]), ("Two", &["g", "h"])]),
    ));
    built(board("tree", LayoutAuthored::Rows(stack)))
}
