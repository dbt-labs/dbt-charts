"""A board's language selects the word catalog its generated words print from."""

from __future__ import annotations

import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from dbt_charts import _engine
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.primitives import LanguageCode, Regional
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render.board_resolve import (
    build_resolved_board_static as resolve_board,
)
from dbt_charts.core.render.layouts import render_tabs_layout
from dbt_charts.core.text.format_d3 import regional_scope


def _regional(**fields: object) -> Regional:
    return Regional.model_validate(fields)


def test_the_binding_formats_plural_messages() -> None:
    assert _engine.message("ru", "series.count", 3) == "3 серии"
    assert _engine.message("de", "tab.title", args={"n": "2"}) == "Tab 2"
    assert "de" in _engine.languages()
    with pytest.raises(ValueError, match="supported: en, de"):
        _engine.message("xx", "tab.title", args={"n": "2"})


@pytest.mark.parametrize(
    ("fields", "language"),
    [
        ({}, "en"),
        ({"locale": {"time": "de-DE"}}, "de"),
        ({"locale": {"time": "zh-TW"}}, "zh"),
        ({"locale": {"time": "nb-NO"}}, "nb"),
        ({"locale": {"time": "de-DE"}, "language": "fr"}, "fr"),
        ({"locale": {"time": "ar-EG"}, "language": "ja"}, "ja"),
    ],
)
def test_the_language_derives_from_the_time_locale_unless_authored(
    fields: dict[str, object], language: str
) -> None:
    assert _regional(**fields).resolved_language == language


def test_a_locale_without_a_catalog_or_a_custom_definition_is_english() -> None:
    assert _regional(locale={"time": "ar-EG"}).resolved_language == "en"
    custom = {
        "date_time": "%x, %X",
        "date": "%Y-%m-%d",
        "time": "%H:%M:%S",
        "periods": ["AM", "PM"],
        "days": ["a", "b", "c", "d", "e", "f", "g"],
        "short_days": ["a", "b", "c", "d", "e", "f", "g"],
        "months": [str(i) for i in range(12)],
        "short_months": [str(i) for i in range(12)],
        "week_start": "monday",
    }
    assert _regional(locale={"time": custom}).resolved_language == "en"
    assert _regional(locale={"time": custom}, language="de").resolved_language == "de"


def test_an_unknown_language_errors_with_the_supported_ones() -> None:
    with pytest.raises(ValidationError, match="Input should be 'en', 'de'"):
        _regional(language="xx")


def test_the_language_codes_are_the_engines_catalogs() -> None:
    assert list(get_args(LanguageCode)) == _engine.languages()


def _tab_bar(local_project: Callable[..., FilesystemProject], regional: str) -> str:
    result = compile(f"""
title: Tabs
{regional}
queries:
  q1:
    type: values
    rows:
      - {{month: Jan, revenue: 100}}
charts:
  c1:
    query: q1
    type: bar
    x: month
    y: revenue
tabs:
  items:
    - title: A
      rows:
        - c1
    - title: B
      rows:
        - c1
""")
    assert result.success
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    reset_config()
    board = resolve_board(result.board)
    with regional_scope(
        board.regional.format_locale,
        board.regional.time_locale,
        board.regional.week_start,
        board.regional.zone,
        language=board.regional.resolved_language,
    ):
        svg, _ = render_tabs_layout(
            board.layout.items,
            executor,
            {},
            800.0,
            600.0,
            tab_titles=[],
            active_tab=0,
            tab_position="top",
            resolved_style=resolve_style(get_theme_style("clarity")),
            render_cache={},
            painted_canvas=None,
        )
    return svg


@pytest.mark.parametrize(
    ("regional", "tab"),
    [
        ("regional:\n  language: fr", "Onglet"),
        ("regional:\n  locale:\n    time: de-DE", "Tab"),
        ("regional:\n  locale:\n    time: ru-RU", "Вкладка"),
        ("", "Tab"),
    ],
)
def test_untitled_tabs_print_their_number_in_the_boards_language(
    local_project: Callable[..., FilesystemProject], regional: str, tab: str
) -> None:
    svg = _tab_bar(local_project, regional)
    assert f">{tab} 1<" in svg
    assert f">{tab} 2<" in svg


def _nested(root: str, nested: str) -> str:
    return f"""
title: Root
regional:
{textwrap.indent(root, "  ")}
rows:
  - title: Nested
    regional:
{textwrap.indent(nested, "      ")}
    text: Hi
"""


def test_a_nested_language_is_compared_resolved() -> None:
    same = compile(_nested("locale:\n  time: de-DE", "language: de"))
    assert same.success
    differs = compile(_nested("locale:\n  time: de-DE", "language: fr"))
    (error,) = differs.errors
    assert error.fields["authored"] == "language: fr"
    assert error.fields["root"] == "language: de"
