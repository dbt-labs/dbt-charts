"""``glob_boards`` / ``glob_folders`` in board text: validated at compile,
resolved by the data provider at render through the one display-text path."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dbt_charts.agent_api.import_closure import board_import_closure
from dbt_charts.core.board_listings import resolve_listings
from dbt_charts.core.compile import compile as compile_text
from dbt_charts.core.compile.compiler import CompileResult, compile_file
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.recording import record_board
from dbt_charts.core.project import Project
from dbt_charts.core.render import render
from dbt_charts.core.render.board_replay import render_board_from_artifact
from dbt_charts.core.render.board_resolve import build_resolved_board
from dbt_charts.core.render.render_result import RenderResult

INDEX = """\
title: Home
text: |
  {% for b in glob_boards("*") %}- [{{ b.title }}]({{ b.path }})
  {% endfor %}
  {% for f in glob_folders("*") %}- [{{ f.title }}]({{ f.path }})
  {% endfor %}
"""


def _project(in_memory_project: Callable[..., Project], index: str) -> Project:
    return in_memory_project(
        Path("/p"),
        {
            "charts/index.yml": index,
            "charts/alpha.yml": "title: Alpha Board\nnotes: first\ntags: [Ops]\n",
            "charts/beta.md": "---\ntitle: Beta Board\n---\nBody\n",
            "charts/_partial.yml": "title: Partial\n",
            "charts/meta.yml": "style: {}\n",
            "charts/sales/index.yml": "title: Sales Home\n",
            "charts/sales/q1.yml": "title: Q1 Board\n",
            "charts/other/deep/x.yml": "title: Deep Board\n",
            "charts/_drafts/wip.yml": "title: WIP\n",
        },
    )


def _compile(project: Project, relpath: str = "charts/index.yml") -> CompileResult:
    return compile_file(project.path(relpath).read_board())


def _render(project: Project, relpath: str = "charts/index.yml") -> RenderResult:
    result = _compile(project, relpath)
    assert result.success, result.errors
    assert result.board is not None
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(project),
        query_registry=result.query_registry,
        clock=lambda: datetime(2001, 2, 3, tzinfo=UTC),
    )
    return render(result.board, executor, format="svg")


def test_glob_boards_lists_matching_boards_with_titles(
    in_memory_project: Callable[..., Project],
) -> None:
    project = _project(in_memory_project, INDEX)
    listings = resolve_listings(
        frozenset({("glob_boards", "*")}), project.directory("charts")
    )
    assert [e.model_dump() for e in listings.boards["*"]] == [
        {"path": "/alpha", "title": "Alpha Board", "notes": "first", "tags": ["ops"]},
        {"path": "/beta", "title": "Beta Board", "notes": "", "tags": []},
    ]


def test_glob_boards_recursive_pattern(
    in_memory_project: Callable[..., Project],
) -> None:
    project = _project(in_memory_project, INDEX)
    listings = resolve_listings(
        frozenset({("glob_boards", "**/*")}), project.directory("charts")
    )
    assert [e.path for e in listings.boards["**/*"]] == [
        "/alpha",
        "/beta",
        "/other/deep/x",
        "/sales/q1",
    ]


def test_glob_boards_is_relative_to_the_listing_board(
    in_memory_project: Callable[..., Project],
) -> None:
    project = _project(in_memory_project, INDEX)
    listings = resolve_listings(
        frozenset({("glob_boards", "*")}), project.directory("charts/sales")
    )
    assert [e.path for e in listings.boards["*"]] == ["/sales/q1"]


def test_glob_folders_titles_from_index_board(
    in_memory_project: Callable[..., Project],
) -> None:
    project = _project(in_memory_project, INDEX)
    listings = resolve_listings(
        frozenset({("glob_folders", "*")}), project.directory("charts")
    )
    assert [e.model_dump() for e in listings.folders["*"]] == [
        {"path": "/other/", "name": "other", "title": "other"},
        {"path": "/sales/", "name": "sales", "title": "Sales Home"},
    ]


def test_listing_renders_into_board_text(
    in_memory_project: Callable[..., Project],
) -> None:
    rendered = _render(_project(in_memory_project, INDEX))
    svg = rendered.output
    assert isinstance(svg, str), rendered.board_error
    for title in ("Alpha Board", "Beta Board", "Sales Home", "other"):
        assert title in svg
    for href in ("/alpha", "/beta", "/sales/", "/other/"):
        assert f'href="{href}"' in svg
    assert "Partial" not in svg
    assert "WIP" not in svg


def test_nonliteral_pattern_is_rejected_at_compile(
    in_memory_project: Callable[..., Project],
) -> None:
    index = (
        "variables:\n  p:\n    input: text\n    default: '*'\n"
        'text: "{{ glob_boards(p) }}"\n'
    )
    result = _compile(_project(in_memory_project, index))
    assert [e.code for e in result.errors] == ["ERR-TEXT-LISTING-NONLITERAL"]


def test_listing_in_title_is_rejected_at_compile(
    in_memory_project: Callable[..., Project],
) -> None:
    index = "title: \"{{ glob_boards('*') | length }} boards\"\n"
    result = _compile(_project(in_memory_project, index))
    assert [e.code for e in result.errors] == ["ERR-TEXT-LISTING-PATTERN"]


def test_pattern_escaping_the_board_root_fails_the_render(
    in_memory_project: Callable[..., Project],
) -> None:
    index = "text: \"{{ glob_boards('../*') }}\"\n"
    rendered = _render(_project(in_memory_project, index))
    assert rendered.board_error is not None
    assert rendered.board_error.code == "ERR-TEXT-LISTING-PATTERN"


def test_import_closure_includes_listed_files(
    in_memory_project: Callable[..., Project],
) -> None:
    project = _project(in_memory_project, INDEX)
    closure = [
        p.relpath for p in board_import_closure(project.path("charts/index.yml"))
    ]
    assert closure == [
        "charts/index.yml",
        "charts/alpha.yml",
        "charts/beta.md",
        "charts/other/deep/x.yml",
        "charts/sales/index.yml",
        "charts/sales/q1.yml",
    ]


def test_listing_in_chart_text_is_rejected_at_compile(
    in_memory_project: Callable[..., Project],
) -> None:
    index = (
        "queries:\n  q:\n    type: values\n    rows:\n      - x: a\n        y: 1\n"
        "charts:\n  c:\n    query: q\n    type: bar\n    x: x\n    y: y\n"
        "    title: \"{{ glob_boards('*') | length }} boards\"\n"
        "rows:\n  - c\n"
    )
    result = _compile(_project(in_memory_project, index))
    assert [e.code for e in result.errors] == ["ERR-TEXT-LISTING-PATTERN"]


@pytest.mark.parametrize(
    "text",
    ["{{ glob_boards }}", "{% set glob_folders = 1 %}{{ glob_folders }}"],
)
def test_listing_name_used_without_a_literal_call_is_rejected_at_compile(
    in_memory_project: Callable[..., Project], text: str
) -> None:
    result = _compile(_project(in_memory_project, f'text: "{text}"\n'))
    assert [e.code for e in result.errors] == ["ERR-TEXT-LISTING-NONLITERAL"]


def test_content_only_tab_listing_is_checked_at_compile(
    in_memory_project: Callable[..., Project],
) -> None:
    index = (
        "variables:\n  p:\n    input: text\n    default: '*'\n"
        'tabs:\n  items:\n    - title: T\n      text: "{{ glob_boards(p) }}"\n'
    )
    result = _compile(_project(in_memory_project, index))
    assert [e.code for e in result.errors] == ["ERR-TEXT-LISTING-NONLITERAL"]


def test_board_without_location_fails_the_render(
    in_memory_project: Callable[..., Project],
) -> None:
    result = compile_text("text: \"{{ glob_boards('*') }}\"\n")
    assert result.success, result.errors
    assert result.board is not None
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(_project(in_memory_project, INDEX)),
        query_registry=result.query_registry,
    )
    rendered = render(result.board, executor, format="svg")
    assert rendered.board_error is not None
    assert rendered.board_error.code == "ERR-TEXT-LISTING-PATTERN"


def test_replay_rejects_a_listing_board_before_rendering(
    in_memory_project: Callable[..., Project],
) -> None:
    project = _project(in_memory_project, INDEX)
    result = _compile(project)
    assert result.board is not None
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(project),
        query_registry=result.query_registry,
    )
    resolved, _ = build_resolved_board(result.board, executor, {})
    recording = record_board(resolved, executor, {})
    with pytest.raises(DbtChartsError) as exc:
        render_board_from_artifact(resolved, recording, {})
    assert exc.value.code.code == "ERR-BOARD-RECORDING-MISMATCH"


def test_import_closure_lists_extends_text_from_the_extending_board(
    in_memory_project: Callable[..., Project],
) -> None:
    project = in_memory_project(
        Path("/p"),
        {
            "charts/_shared/_tpl.yml": "text: \"{{ glob_boards('*') }}\"\n",
            "charts/finance/rev.yml": "extends: ../_shared/_tpl.yml\ntitle: Rev\n",
            "charts/finance/cost.yml": "title: Cost\n",
        },
    )
    closure = [
        p.relpath for p in board_import_closure(project.path("charts/finance/rev.yml"))
    ]
    assert closure == [
        "charts/finance/rev.yml",
        "charts/_shared/_tpl.yml",
        "charts/finance/cost.yml",
    ]


def test_import_closure_lists_a_shared_template_from_each_extending_folder(
    in_memory_project: Callable[..., Project],
) -> None:
    project = in_memory_project(
        Path("/p"),
        {
            "charts/_shared/_nav.yml": "text: \"{{ glob_boards('*') }}\"\n",
            "charts/index.yml": (
                "extends: _shared/_nav.yml\nrows:\n  - sales/index.yml\n"
            ),
            "charts/home2.yml": "title: Home 2\n",
            "charts/sales/index.yml": "extends: ../_shared/_nav.yml\n",
            "charts/sales/pipeline.yml": "title: Pipeline\n",
        },
    )
    closure = [
        p.relpath for p in board_import_closure(project.path("charts/index.yml"))
    ]
    assert closure == [
        "charts/index.yml",
        "charts/_shared/_nav.yml",
        "charts/home2.yml",
        "charts/sales/index.yml",
        "charts/sales/pipeline.yml",
    ]
