"""Which tabs a render executes.

``scope_tabs`` returns the board with every tab a tabs layout does not show
emptied. A
render executes, resolves and checks that board, so query collection, the
category-color plan, chart resolution and the warning pass all walk one tree
and none of them can reach a query the pre-render pass did not run.
"""

from dbt_charts.core.compile.models.board.authored import LayoutType
from dbt_charts.core.compile.models.board.normalized import (
    Board,
    Layout,
    LayoutItem,
    VariableValues,
)
from dbt_charts.core.render.sizing import resolve_active_tab_index


def scope_tabs(board: Board, variables: VariableValues) -> Board:
    """``board`` with each tab other than the one its layout shows emptied.

    An emptied tab keeps its place, so tab indices, slugs and the active-tab
    choice are unchanged, and keeps its board's variables, whose controls are
    board-global. Returns ``board`` itself when nothing is emptied.
    """
    layout = _scope_layout(board.layout, variables)
    if layout is board.layout:
        return board
    return board.model_copy(update={"layout": layout})


def _scope_layout(layout: Layout, variables: VariableValues) -> Layout:
    narrowed = layout.type == "tabs" and bool(layout.items)
    active = (
        resolve_active_tab_index(
            len(layout.items),
            layout.tab_variable,
            layout.tab_slugs,
            layout.default_tab,
            variables,
        )
        if narrowed
        else None
    )
    items = [
        _emptied(item)
        if active is not None and index != active
        else _scoped(item, variables)
        for index, item in enumerate(layout.items)
    ]
    if all(new is old for new, old in zip(items, layout.items, strict=True)):
        return layout
    return layout.model_copy(update={"items": items})


def _scoped(item: LayoutItem, variables: VariableValues) -> LayoutItem:
    if item.board is None:
        return item
    board = scope_tabs(item.board, variables)
    return item if board is item.board else item.model_copy(update={"board": board})


def _emptied(tab: LayoutItem) -> LayoutItem:
    """A tab that draws nothing and reads no query; every tab is a board."""
    if tab.board is None:
        raise AssertionError("a tabs layout item is always a board")
    board = tab.board.model_copy(
        update={
            "layout": Layout(type=LayoutType.ROWS, items=[]),
            "title": "",
            "text": "",
        }
    )
    return tab.model_copy(update={"board": board})
