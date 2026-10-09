"""Generated style patches enforce the field rules of the theme class they mirror."""

from __future__ import annotations

from typing import Any, get_args

import pytest
from pydantic import ValidationError
from pygments.styles import STYLE_MAP

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.board.authored import AUTHORED_BOARD_ADAPTER
from dbt_charts.core.compile.models.board.patch import BOARD_PATCH_ADAPTER
from dbt_charts.core.compile.models.factories import build_patch_model
from dbt_charts.core.compile.models.primitives import DividerStyle
from dbt_charts.core.compile.models.style.theme.board import PygmentsTheme, TitleStyle
from dbt_charts.core.compile.models.style.theme.legend import LegendStyle


def _chart_board(chart: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": "t",
        "queries": {"q": {"sql": "select 1 as a, 2 as b"}},
        "charts": {"c": {"query": "q", **chart}},
        "rows": ["c"],
    }


def _bar(style: dict[str, Any]) -> dict[str, Any]:
    return _chart_board({"type": "bar", "x": "a", "y": "b", "style": style})


def _board(style: dict[str, Any]) -> dict[str, Any]:
    return {"title": "t", "text": "x", "style": style}


@pytest.mark.parametrize(
    ("board", "message"),
    [
        (_bar({"legend": {"symbol_limit": 0}}), "greater than or equal to 1"),
        (
            _chart_board(
                {
                    "type": "pie",
                    "theta": "b",
                    "color": "a",
                    "style": {"inner_radius": 2},
                }
            ),
            "less than or equal to 1",
        ),
        (_board({"frame": {"width": -5}}), "greater than 0"),
        (
            _board({"title": {"level": 0}}),
            "title.level must be 'auto' or a positive integer, got 0",
        ),
        (_board({"text": {"bold": {"weight": "heavy"}}}), "'normal', 'bold'"),
        (_board({"text": {"code": {"theme": "nope"}}}), "'monokai'"),
        (
            _board({"text": {"divider": {"dasharray": "a b"}}}),
            "dasharray must be positive numbers such as '6 3', got 'a b'",
        ),
        (_bar({"axis_x": {"fiscal_year_start_month": 13}}), "less than or equal to 12"),
        (
            _bar({"color": {"categorical": {"single_series_palette": []}}}),
            "single_series_palette must be a palette name or a non-empty list "
            "of colors, got \\[\\]",
        ),
        (
            _chart_board(
                {
                    "type": "pie",
                    "theta": "b",
                    "color": "a",
                    "style": {"marks": {"slice": {"labels": {"template": "{{ x"}}}},
                }
            ),
            "unexpected end of template",
        ),
    ],
    ids=[
        "legend-symbol-limit",
        "pie-inner-radius",
        "frame-width",
        "title-level",
        "bold-weight",
        "code-theme",
        "divider-dasharray",
        "fiscal-month",
        "single-series-palette",
        "pie-label-template",
    ],
)
def test_parse_rejects_theme_forbidden_style_value(
    board: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        AUTHORED_BOARD_ADAPTER.validate_python(board)


@pytest.mark.parametrize(
    ("style", "field"),
    [
        ({"title": {"level": 0}}, "level"),
        ({"text": {"divider": {"dasharray": "a b"}}}, "dasharray"),
        (
            {"charts": {"color": {"categorical": {"single_series_palette": []}}}},
            "single_series_palette",
        ),
    ],
)
def test_a_union_field_reports_one_error(style: dict[str, Any], field: str) -> None:
    with pytest.raises(ValidationError) as caught:
        AUTHORED_BOARD_ADAPTER.validate_python(_board(style))
    assert [e["loc"][-1] for e in caught.value.errors()] == [field]


# Lax like every authored int: "2" and true coerce, as patches always allowed.
@pytest.mark.parametrize(("authored", "level"), [("2", 2), (True, 1)])
def test_title_level_coerces_lax_input_on_theme_and_patch(
    authored: object, level: int
) -> None:
    theme = get_theme_style().title.model_dump(mode="python")
    assert TitleStyle.model_validate({**theme, "level": authored}).level == level
    patch = build_patch_model(TitleStyle).model_validate({"level": authored})
    assert patch.model_dump(exclude_unset=True) == {"level": level}


def test_patch_null_stays_unconstrained() -> None:
    patch = build_patch_model(LegendStyle).model_validate({"symbol_limit": None})
    assert patch.model_dump(exclude_unset=True) == {"symbol_limit": None}


def test_divider_dasharray_normalizes_separators() -> None:
    assert (
        DividerStyle(visible=True, color="#000", width=1, dasharray=" 6, 3 ").dasharray
        == "6 3"
    )
    patch = build_patch_model(DividerStyle).model_validate({"dasharray": "6,3"})
    assert patch.model_dump(exclude_unset=True) == {"dasharray": "6,3"}


def test_pygments_theme_names_every_built_in_style() -> None:
    assert set(get_args(PygmentsTheme)) == set(STYLE_MAP)


def test_a_shorthand_field_desugars_once_on_its_patch() -> None:
    patch = BOARD_PATCH_ADAPTER.validate_python({"cache": "1h"})
    assert (patch.cache.enabled, patch.cache.ttl) == (True, "1h")
