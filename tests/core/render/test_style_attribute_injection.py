"""Authored style strings render without breaking the SVG or HTML around them."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.project import Project
from dbt_charts.core.render import render
from dbt_charts.core.render.render_result import RenderResult


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
