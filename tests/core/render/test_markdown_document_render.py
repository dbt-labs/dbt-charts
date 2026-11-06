"""A markdown file with no ``board:`` key is a document, end to end through
``render_dashboard``: the Jinja it quotes prints instead of rendering blank."""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

from dbt_charts.core.board import render_dashboard
from dbt_charts.core.execute.env import build_exec_env

from ...conftest import InMemoryProject

_NOTE = """\
# Modeling notes

Reference a model with `{{ ref('orders') }}`, or write {{ ref('orders') }} bare.
Use {% raw %} and {# a comment #} literally.
"""


def _text(markdown: str) -> str:
    project = InMemoryProject(Path("/proj"), {"charts/note.md": markdown})
    result = render_dashboard(
        board=project.path("charts/note.md").read_board(),
        variables=None,
        project=project,
        env=build_exec_env(project, read_only=False),
        result_cache=None,
        format="svg",
    )
    assert result.status == "ok", result.chart_errors or result.validation_errors
    assert isinstance(result.data, str)
    return " ".join("".join(ElementTree.fromstring(result.data).itertext()).split())


def test_a_document_prints_the_jinja_it_quotes() -> None:
    text = _text(_NOTE)
    assert "{{ ref('orders') }}, or write {{ ref('orders') }} bare." in text
    assert "Use {% raw %} and {# a comment #} literally." in text


def test_a_board_key_keeps_the_body_a_template() -> None:
    assert "Total: 2." in _text("---\nboard:\n  title: T\n---\n\nTotal: {{ 1 + 1 }}.\n")
