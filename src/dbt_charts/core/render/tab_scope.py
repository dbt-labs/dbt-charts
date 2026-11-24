"""Which tabs a render executes.

``scope_tabs`` returns the board with every tab a tabs layout does not show
emptied. A
render executes, resolves and checks that board, so query collection, the
category-color plan, chart resolution and the warning pass all walk one tree
and none of them can reach a query the pre-render pass did not run.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.board.authored import LayoutType
from dbt_charts.core.compile.models.board.normalized import (
    Board,
    Layout,
    LayoutItem,
    VariableValues,
)
from dbt_charts.core.execute.chart_data_provider import ChartDataProvider
from dbt_charts.core.render.conditions import evaluate_visible
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


def drop_hidden_tabs(
    board: Board,
    variables: VariableValues,
    results: ChartDataProvider,
    *,
    every_tab: bool,
) -> tuple[Board, VariableValues]:
    """``board`` without the tabs whose ``visible:`` is falsy, and ``variables``
    with each selection of a hidden default moved to the first drawn tab.

    A ``{query, column}`` probe reads ``results``. Only a drawn tab bar reads
    one: under a closed tab nothing is dropped, unless ``every_tab`` (a format
    that prints every tab) draws them all. A probe whose query was not
    collected raises.
    """
    selected = dict(variables)
    layout = _drop_hidden(board.layout, variables, selected, results, every_tab)
    if layout is board.layout:
        return board, variables
    return board.model_copy(update={"layout": layout}), selected


def _drop_hidden(
    layout: Layout,
    variables: VariableValues,
    selected: VariableValues,
    results: ChartDataProvider,
    every_tab: bool,
) -> Layout:
    if layout.type != "tabs":
        items = [
            _dropped(item, variables, selected, results, every_tab)
            for item in layout.items
        ]
        if all(new is old for new, old in zip(items, layout.items, strict=True)):
            return layout
        return layout.model_copy(update={"items": items})
    titles, slugs, variable = layout.tab_titles, layout.tab_slugs, layout.tab_variable
    if titles is None or slugs is None or variable is None:
        raise AssertionError("a tabs layout carries titles, slugs and a variable")
    kept = [
        index
        for index, item in enumerate(layout.items)
        if evaluate_visible(item.visible, variables, results)
    ]
    value = variables.get(variable)
    # The first tab's own slug is the default, which falls back to the first
    # drawn tab; any other hidden slug stays selected and is refused as no tab.
    if kept and value and str(value) == slugs[0] and 0 not in kept:
        selected[variable] = slugs[kept[0]]
    chosen = selected.get(variable)
    opened = slugs.index(str(chosen)) if chosen and str(chosen) in slugs else 0
    if opened not in kept and kept:
        opened = kept[0]
    items = [
        _dropped(layout.items[index], variables, selected, results, every_tab)
        if every_tab or index == opened
        else layout.items[index]
        for index in kept
    ]
    if len(kept) == len(layout.items) and all(
        new is old for new, old in zip(items, layout.items, strict=True)
    ):
        return layout
    return layout.model_copy(
        update={
            "items": items,
            "tab_titles": [titles[index] for index in kept],
            "tab_slugs": [slugs[index] for index in kept],
        }
    )


def _dropped(
    item: LayoutItem,
    variables: VariableValues,
    selected: VariableValues,
    results: ChartDataProvider,
    every_tab: bool,
) -> LayoutItem:
    if item.board is None:
        return item
    layout = _drop_hidden(item.board.layout, variables, selected, results, every_tab)
    if layout is item.board.layout:
        return item
    board = item.board.model_copy(update={"layout": layout})
    return item.model_copy(update={"board": board})
