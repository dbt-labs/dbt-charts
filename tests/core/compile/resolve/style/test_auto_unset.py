"""`auto` on a renderer-default leaf reads as unset once the cascade has run."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.resolve.style.board import (
    merge_style,
    resolve_style_and_context,
)


def _context(patch: dict[str, object] | None = None) -> ChartStyleContext:
    style = None if patch is None else StylePatch.model_validate(patch)
    return resolve_style_and_context(get_theme_style(get_default_theme_name()), style)[
        1
    ]


def test_theme_auto_leaves_are_unset_in_the_resolved_context() -> None:
    context = _context()
    assert context.axis.labels.angle is None
    assert context.marks.point.size is None
    assert context.marks.bar.total_label.visible is None
    assert context.cascaded.charts.legend.direction is None
    assert context.cascaded.title.position.baseline is None


def test_an_auto_that_names_an_engine_decision_stays_auto() -> None:
    assert _context().cascaded.charts.legend.visible == "auto"


def test_a_board_value_over_an_auto_leaf_wins() -> None:
    context = _context({"charts": {"axis": {"labels": {"angle": 30}}}})
    assert context.axis.labels.angle == 30


def test_auto_written_over_a_pinned_leaf_resets_it() -> None:
    theme = get_theme_style(get_default_theme_name())
    pinned = merge_style(
        theme,
        StylePatch.model_validate({"charts": {"axis": {"labels": {"angle": 30}}}}),
    )
    assert resolve_style_and_context(pinned)[1].axis.labels.angle == 30
    reset = StylePatch.model_validate(
        {"charts": {"axis": {"labels": {"angle": "auto"}}}}
    )
    assert resolve_style_and_context(pinned, reset)[1].axis.labels.angle is None


@pytest.mark.parametrize(
    "path", ["charts.axis.labels.angle", "charts.marks.point.size"]
)
def test_every_unset_leaf_is_total_in_the_theme(path: str) -> None:
    node = get_theme_style(get_default_theme_name())
    for part in path.split("."):
        node = getattr(node, part)
    assert node == "auto"


@pytest.mark.parametrize(
    ("patch", "field"),
    [
        ({"charts": {"legend": {"symbol_shape": "blob"}}}, "symbol_shape"),
        ({"charts": {"marks": {"point": {"shape": "M0,0L1,1"}}}}, "shape"),
        ({"title": {"position": {"baseline": "sideways"}}}, "baseline"),
    ],
)
def test_a_named_value_leaf_rejects_an_unknown_value_naming_the_field(
    patch: dict[str, object], field: str
) -> None:
    with pytest.raises(ValueError, match=field):
        StylePatch.model_validate(patch)


@pytest.mark.parametrize(
    "patch",
    [
        {"charts": {"legend": {"symbol_shape": "auto"}}},
        {"charts": {"marks": {"point": {"shape": "diamond"}}}},
        {"title": {"position": {"baseline": "line-top"}}},
    ],
)
def test_a_named_value_leaf_accepts_auto_and_its_names(
    patch: dict[str, object],
) -> None:
    StylePatch.model_validate(patch)
