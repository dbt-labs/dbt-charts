"""Authored style strings must not break out of the SVG attributes they fill."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.project import Project
from dbt_charts.core.render import render
from dbt_charts.core.render.render_result import RenderResult

_PAYLOAD = 'x" onmouseover="alert(1)'


def _board(font_key: str) -> str:
    return f"""\
title: "Report `code`"
html_policy: safe-subset
style:
  font:
    {font_key}: '{_PAYLOAD}'
  text:
    code:
      background: '{_PAYLOAD}'
  variables:
    font:
      color: '{_PAYLOAD}'
  charts:
    table:
      spark:
        bar:
          label:
            fill: '{_PAYLOAD}'
variables:
  product_name:
    input: text
queries:
  q: {{type: values, columns: [n, cat, val], values: [[1, "A", 5]]}}
charts:
  t:
    query: q
    type: table
    style:
      columns:
        val:
          spark:
            type: bar
            value_visible: true
  k: {{query: q, type: kpi, value: n}}
  c: {{type: callout, title: Heads up, message: Something happened}}
  sb: {{query: q, type: spark_bar, x: val, y: cat}}
rows: [t, k, c, sb]
"""


def _render_result(yaml: str, local_project: Callable[..., Project]) -> RenderResult:
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


def _render_svg(yaml: str, local_project: Callable[..., Project]) -> str:
    rendered = _render_result(yaml, local_project)
    assert isinstance(rendered.output, str)
    return rendered.output


def test_font_family_style_cannot_inject_event_handler(
    local_project: Callable[..., Project],
) -> None:
    svg = _render_svg(_board("family"), local_project)
    handlers = [
        el.tag
        for el in ET.fromstring(svg).iter()
        if any(attr.startswith("on") for attr in el.attrib)
    ]
    assert handlers == []


def test_invalid_font_color_becomes_a_clean_board_diagnostic(
    local_project: Callable[..., Project],
) -> None:
    """style.font.color feeds the title fill (a style="..." sink). A value
    that cannot parse as a CSS color — here, an attempted event-handler
    injection payload — must not reach the attribute at all: css_color
    raises, and the board render surfaces a structured
    ERR-CSS-COLOR-INVALID-AT-RENDER diagnostic instead of a live style value
    or an uncaught traceback."""
    rendered = _render_result(_board("color"), local_project)
    assert rendered.output is None
    assert rendered.board_error is not None
    assert rendered.board_error.code == "ERR-CSS-COLOR-INVALID-AT-RENDER"


_STYLE_BLOCK_PAYLOAD = "x</style><img src=x onerror=alert(1)>"


def test_font_family_cannot_break_out_of_style_block(
    local_project: Callable[..., Project],
) -> None:
    """style.font.family also reaches mdsvg's <style> block (a callout's
    markdown renderer); a quote-free payload must not close the element and
    inject an <img>."""
    yaml = f"""\
title: Report
style:
  font:
    family: "{_STYLE_BLOCK_PAYLOAD}"
queries:
  q: {{type: values, rows: [{{n: 1}}]}}
charts:
  t: {{query: q, type: table}}
  c: {{type: callout, title: Heads up, message: Something happened}}
rows: [t, c]
"""
    svg = _render_svg(yaml, local_project)
    root = ET.fromstring(svg)
    elements = list(root.iter())
    assert not any(el.tag.endswith("}img") for el in elements)
    assert not any(attr.startswith("on") for el in elements for attr in el.attrib)


def test_html_export_font_family_cannot_break_out_of_style_block(
    local_project: Callable[..., Project],
) -> None:
    """format="html" wraps the canonical SVG's data-dbt-font-family attribute
    into templates/page.css, inside a <style> block: Jinja's autoescape does
    not cover .css includes, and HTML entities wouldn't decode inside a raw-
    text <style> element even if it did."""
    from dbt_charts.core.render.converters.html import to_html

    yaml = f"""\
title: Report
style:
  font:
    family: "{_STYLE_BLOCK_PAYLOAD}"
queries:
  q: {{type: values, rows: [{{n: 1}}]}}
charts:
  t: {{query: q, type: table}}
rows: [t]
"""
    svg = _render_svg(yaml, local_project)
    html = to_html(svg)
    # Checked against the WHOLE document: the payload's own </style> would
    # end a "first </style>" slice early and hide the <img> that follows it.
    assert _STYLE_BLOCK_PAYLOAD not in html
    assert "</style><img" not in html


@pytest.mark.parametrize("payload", [_STYLE_BLOCK_PAYLOAD, "red /* x"])
def test_html_export_css_breaking_background_raises(
    payload: str, local_project: Callable[..., Project]
) -> None:
    from dbt_charts.core.render.converters.html import to_html
    from dbt_charts.core.render.errors import RenderError

    yaml = f"""\
title: Report
style:
  background: "{payload}"
queries:
  q: {{type: values, rows: [{{n: 1}}]}}
charts:
  t: {{query: q, type: table}}
rows: [t]
"""
    svg = _render_svg(yaml, local_project)
    with pytest.raises(RenderError):
        to_html(svg)


def test_html_export_css_function_background_exports_verbatim(
    local_project: Callable[..., Project],
) -> None:
    from dbt_charts.core.render.converters.html import to_html

    yaml = """\
title: Report
style:
  background: "var(--bg)"
queries:
  q: {type: values, rows: [{n: 1}]}
charts:
  t: {query: q, type: table}
rows: [t]
"""
    svg = _render_svg(yaml, local_project)
    html = to_html(svg)
    assert "background-color: var(--bg);" in html


_BACKSLASH_ESCAPE_PAYLOAD = r"x\042 onmouseover=\042alert(1)"


def test_title_color_backslash_sequence_cannot_reach_re_sub_replacement(
    local_project: Callable[..., Project],
) -> None:
    """style.title.font.color is painted onto the title SVG via re.sub; a
    replacement STRING (not a callable) processes its own backslash escapes,
    so an escaped `\\042` could still decode to a literal `"` and break out
    of the `style="fill: ..."` attribute even after escape_attr. Not a valid
    CSS color either way, so css_color raises before re.sub ever runs — a
    clean board diagnostic, not a live style value or a traceback."""
    yaml = f"""\
title: Report
style:
  title:
    font:
      color: '{_BACKSLASH_ESCAPE_PAYLOAD}'
queries:
  q: {{type: values, rows: [{{n: 1}}]}}
charts:
  t: {{query: q, type: table}}
rows: [t]
"""
    rendered = _render_result(yaml, local_project)
    assert rendered.output is None
    assert rendered.board_error is not None
    assert rendered.board_error.code == "ERR-CSS-COLOR-INVALID-AT-RENDER"


def test_unicode_variable_name_renders_fine(
    local_project: Callable[..., Project],
) -> None:
    """A Unicode variable name is a legal Jinja identifier and a legal XML
    attribute name (no markup-breaking character) — it must render, not raise.
    """
    yaml = """\
title: Test board
variables:
  café:
    input: text
    default: x
charts:
  t: {type: callout, title: "{{ café }}", message: hello}
rows: [t]
"""
    svg = _render_svg(yaml, local_project)
    assert 'data-var-café="true"' in svg


def test_attr_name_rejects_markup_breaking_characters() -> None:
    from dbt_charts.core.render.errors import RenderError
    from dbt_charts.core.render.svg_utils import attr_name

    for name in ("a b", 'a="b"', "a<b", "a/b"):
        with pytest.raises(RenderError):
            attr_name(name)
