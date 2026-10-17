"""A custom chart's `style` tree carries resolved values, never engine-decided literals."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.chart.custom import _style_tree
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context


def _leaves(
    node: Any, path: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaves(value, (*path, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _leaves(value, (*path, str(index)))
    else:
        yield path, node


@pytest.mark.parametrize("theme", ["clarity", "paper", "stark", "neon", "vivid"])
def test_style_tree_carries_no_engine_decided_literal(theme: str) -> None:
    tree = _style_tree(resolve_chart_style_context(get_theme_style(theme)))
    leaked = [
        ".".join(path)
        for path, value in _leaves(tree)
        # Other `auto` leaves (heading level, axis position, tick stubs) keep
        # their documented meaning.
        if value == "auto"
        and (
            {"legend", "endpoint_labels"} & set(path)
            or path[-1] in {"count", "format"}
            or path[-3:-1] == ("title", "font")
            or path[-2:] == ("title", "visible")
            or path[-2:] == ("font", "color")
            or path[-2:] in {("footer", "text"), ("timestamp", "format")}
            or path[-3:] == ("placeholder", "overlay", "text")
        )
    ]
    assert not leaked, leaked


def test_family_legend_position_inherits_the_board_general_position() -> None:
    from dbt_charts.core.compile.models.style.authored import StylePatch

    board_patch = StylePatch.model_validate(
        {
            "charts": {
                "legend": {"position": {"edge": "bottom"}},
                "line": {"legend": {"position": {"align": "end"}}},
            }
        }
    )
    tree = _style_tree(
        resolve_chart_style_context(get_theme_style("clarity"), board_patch)
    )
    position = tree["charts"]["line"]["legend"]["position"]
    assert (position["edge"], position["align"]) == ("bottom", "end")


def _rendered_position(chart_def: dict[str, Any], board_patch: Any) -> tuple[Any, ...]:
    from dbt_charts.core.compile.models.query.normalized import ValuesQuery
    from dbt_charts.core.compile.normalize.charts import normalize_chart
    from dbt_charts.core.compile.resolve import resolve

    rows = [{"m": "a", "c": "A", "v": 1.0}, {"m": "b", "c": "B", "v": 2.0}]
    flat = normalize_chart("c1", chart_def, {"q": ValuesQuery(rows=rows)}, sources={})
    ctx = resolve_chart_style_context(get_theme_style("clarity"), board_patch)
    position = resolve(flat, rows, chart_style_context=ctx).legend.position
    return (position.edge, position.align, position.overlay)


def _tree_position(family: str, board_patch: Any) -> tuple[Any, ...]:
    ctx = resolve_chart_style_context(get_theme_style("clarity"), board_patch)
    position = _style_tree(ctx)["charts"][family]["legend"]["position"]
    return (position["edge"], position["align"], position["overlay"])


@pytest.mark.parametrize(
    ("family", "chart_def", "legend"),
    [
        (
            "line",
            {"type": "line", "query": "q", "x": "m", "y": "v", "color": "c"},
            {"position": {"edge": "auto"}},
        ),
        (
            "pie",
            {"type": "pie", "query": "q", "theta": "v", "color": "c"},
            {"position": {"edge": "left"}},
        ),
    ],
)
def test_family_legend_position_matches_the_chart_resolver(
    family: str, chart_def: dict[str, Any], legend: dict[str, Any]
) -> None:
    from dbt_charts.core.compile.models.style.authored import StylePatch

    board_patch = StylePatch.model_validate(
        {
            "charts": {
                "legend": {"position": {"edge": "bottom"}},
                family: {"legend": legend},
            }
        }
    )
    assert _tree_position(family, board_patch) == _rendered_position(
        chart_def, board_patch
    )
