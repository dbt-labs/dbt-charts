"""Authored color and font-family strings are checked before they reach the SVG."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import BaseModel

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.models.markers import Color
from dbt_charts.core.compile.models.primitives import ColumnFontStyle
from dbt_charts.core.compile.models.style.authored.table import (
    TableColumnConfig,
    TableColumnDefaultsConfig,
)
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.fonts import check_authored_font_stack
from dbt_charts.core.project import Project
from dbt_charts.core.render import render
from dbt_charts.core.render.render_result import RenderResult

_BODY = """
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - revenue
"""


def _background(value: str) -> str:
    return f"style:\n  background: {value}\n{_BODY}"


def _nested_color(value: str) -> str:
    return f"style:\n  font:\n    color: {value}\n{_BODY}"


@pytest.mark.parametrize(
    "value",
    [
        "notacolor",
        '"linear-gradient(red, blue)"',
        '"url(x.png)"',
        '"{{ missing_var }}"',
        '"red;}"',
    ],
)
@pytest.mark.parametrize("build", [_background, _nested_color])
def test_color_field_rejects_non_color_values(
    value: str, build: Callable[[str], str]
) -> None:
    result = compile(build(value))
    assert not result.success
    assert any(value.strip('"') in str(e) for e in result.errors), result.errors


@pytest.mark.parametrize(
    "value",
    [
        "'#abc'",
        "'#aabbcc'",
        "'#aabbccdd'",
        "red",
        "RebeccaPurple",
        "transparent",
        "none",
        "currentColor",
        "dbt-grays.3",
        "'oklch(0.7 0.1 250)'",
        "'var(--x)'",
        "'rgb(10 20 30 / 50%)'",
        '""',
    ],
)
def test_color_field_keeps_readable_and_unreadable_colors(value: str) -> None:
    result = compile(_background(value))
    assert result.success, result.errors


def test_unknown_color_name_error_names_the_field_path() -> None:
    result = compile(_background("notacolor"))
    assert [e.code for e in result.errors] == ["ERR-COLOR-TOKEN"]
    assert "style.background" in result.errors[0].message
    assert "notacolor" in result.errors[0].message


def test_unknown_color_name_in_a_chart_style_names_the_chart(
    local_project: Callable[..., Project],
) -> None:
    body = """
queries:
  q: {type: values, columns: [month, revenue], values: [[Jan, 100]]}
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      color:
        static: notacolor
rows: [revenue]
"""
    rendered = _render(body, local_project)
    assert [d.code for d in rendered.chart_errors] == ["ERR-COLOR-TOKEN"]
    assert "'revenue'" in rendered.chart_errors[0].message


def test_table_column_color_may_name_a_column() -> None:
    result = compile(
        """
queries:
  q: {type: values, columns: [status, tone], values: [[ok, "#0a0"]]}
charts:
  t:
    query: q
    type: table
    style:
      columns:
        status:
          background: tone
          font:
            color: tone
rows: [t]
"""
    )
    assert result.success, result.errors


_FAMILY_BOARD = """
title: T
style:
  font:
    family: Source Serif 4, serif
queries:
  q: {type: values, columns: [n], values: [[1]]}
charts:
  k: {query: q, type: kpi, value: n}
rows: [k]
"""


def _render(yaml: str, local_project: Callable[..., Project]) -> RenderResult:
    result = compile(yaml)
    assert result.board is not None
    return render(
        result.board,
        Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        ),
        format="svg",
    )


def test_font_family_with_digit_word_is_quoted_in_the_svg(
    local_project: Callable[..., Project],
) -> None:
    rendered = _render(_FAMILY_BOARD, local_project)
    assert isinstance(rendered.output, str)
    families = [
        el.attrib["font-family"]
        for el in ET.fromstring(rendered.output).iter()
        if "font-family" in el.attrib
    ]
    ours = [f for f in families if "Source Serif 4" in f]
    assert ours
    assert all("'Source Serif 4'" in f for f in ours), ours


@pytest.mark.parametrize("family", ['"a;b"', "'a{b}'", "'a<b'", "'a*b'"])
def test_font_family_rejects_css_breaking_characters(family: str) -> None:
    result = compile(f"style:\n  font:\n    family: {family}\n{_BODY}")
    assert not result.success


@pytest.mark.parametrize(
    "style",
    [
        "font:\n    family: 'x\" onmouseover=\"alert(1)'",
        'font:\n    family: "x</style><img src=x onerror=alert(1)>"',
        "font:\n    color: 'x\" onmouseover=\"alert(1)'",
        'background: "x</style><img src=x onerror=alert(1)>"',
        'background: "red /* x"',
        "title:\n    font:\n      color: 'x\\042 onmouseover=\\042alert(1)'",
        'charts:\n    callout:\n      message:\n        font:\n          color: "red;} svg{background:url(//evil)}"',
    ],
)
def test_hostile_style_string_is_rejected_at_compile(style: str) -> None:
    result = compile(f"style:\n  {style}\n{_BODY}")
    assert result.board is None
    assert result.errors


@pytest.mark.parametrize(
    "value", ['"rgb(1 2 3 /* x)"', '"rgb(1 2 3 \\n)"', "'rgb(1 2 3)\\u0001'"]
)
def test_color_function_body_rejects_comment_and_control_characters(
    value: str,
) -> None:
    assert not compile(_background(value)).success


def test_named_font_color_must_be_a_color() -> None:
    result = compile(
        """
style:
  fonts:
    brand:
      color: notacolor
  title:
    font:
      extends: brand
queries:
  q: {type: values, columns: [n], values: [[1]]}
charts:
  k: {query: q, type: kpi, value: n}
rows: [k]
"""
    )
    assert [e.code for e in result.errors] == ["ERR-COLOR-TOKEN"]
    assert "style.fonts.brand.color" in result.errors[0].message


@pytest.mark.parametrize(
    ("family", "expected"),
    [
        ("Source Serif 4, serif", "'Source Serif 4', serif"),
        ("'Times New Roman', Georgia, serif", "'Times New Roman', Georgia, serif"),
        ("Helvetica Neue, Arial", "Helvetica Neue, Arial"),
    ],
)
def test_font_stack_is_canonicalized(family: str, expected: str) -> None:
    assert check_authored_font_stack(family) == expected


@pytest.mark.parametrize(
    "family",
    [
        "O'Reilly Sans",
        "'X, serif",
        "'Foo, Bar', serif",
        "a,,b",
        "'a'b'",
        "a\x01b",
        "a\x7fb",
    ],
)
def test_font_stack_rejects_what_a_browser_would_drop(family: str) -> None:
    with pytest.raises(ValueError, match="font family|quote|empty|control"):
        check_authored_font_stack(family)


_TABLE = """
queries:
  q: {type: values, columns: [status, "Tone 1", delta], values: [[ok, "#0a0", -1]]}
charts:
  t:
    query: q
    type: table
    %s
rows: [t]
"""


@pytest.mark.parametrize(
    "block",
    [
        "style:\n      column_defaults:\n        background: Tone 1\n        font:\n          color: Tone 1",
        "style:\n      columns:\n        status:\n          background: Tone 1\n          font:\n            color: Tone 1",
    ],
)
def test_table_column_first_colors_may_name_any_column(
    block: str, local_project: Callable[..., Project]
) -> None:
    assert _render(_TABLE % block, local_project).chart_errors == []


@pytest.mark.parametrize(
    "block",
    [
        "style:\n      font:\n        color: notacolor",
        "style:\n      columns:\n        status:\n          glyph: x\n          glyph_color: notacolor",
        'conditional_formatting:\n      delta:\n        when:\n          - {lt: 0, glyph: "x", glyph_color: notacolor}',
    ],
)
def test_literal_color_fields_reject_unknown_names(
    block: str, local_project: Callable[..., Project]
) -> None:
    rendered = _render(_TABLE % block, local_project)
    assert [d.code for d in rendered.chart_errors] == ["ERR-COLOR-TOKEN"]


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (TableColumnConfig, "background"),
        (TableColumnDefaultsConfig, "background"),
        (ColumnFontStyle, "color"),
    ],
)
def test_column_first_colors_keep_the_color_facet(
    model: type[BaseModel], field: str
) -> None:
    """The design panel offers a swatch from the facet, so it must stay."""
    facets = [m for m in model.model_fields[field].metadata if isinstance(m, Color)]
    assert [f.column_ref for f in facets] == [True]
    assert facets[0].pattern is None
