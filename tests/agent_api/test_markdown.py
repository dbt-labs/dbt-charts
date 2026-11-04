"""Each ``charts`` fence of a markdown page, rendered on its own."""

from collections.abc import Callable
from pathlib import Path

from dbt_charts.agent_api import ProjectSession
from dbt_charts.agent_api.markdown import (
    render_chart_fences,
    render_markdown_with_images,
    splice_lines,
)
from dbt_charts.cli.filesystem_project import FilesystemProject

SHARED_QUERY_PAGE = """\
# Revenue

```charts
queries:
  sales:
    columns: [month, revenue]
    values:
      - [Jan, 100]
      - [Feb, 150]
charts:
  by_month:
    title: Revenue by month
    type: bar
    query: sales
    x: month
    y: revenue
rows:
  - by_month
```

The same numbers as a line.

```charts
type: line
query: sales
x: month
y: revenue
```
"""


def _page(tmp_path: Path, text: str) -> Path:
    page = tmp_path / "docs" / "post.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(text)
    return page


def test_fences_share_a_query_and_render_apart(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    _page(tmp_path, SHARED_QUERY_PAGE)
    project = local_project(tmp_path)

    with ProjectSession.from_project(project) as session:
        result = render_chart_fences(
            session, project.path("docs/post.md").read_board(named=True), False
        )

    assert result.errors == []
    assert [(f.first_line, f.last_line) for f in result.fences] == [(2, 18), (22, 27)]
    assert all(f.svg.startswith("<svg") for f in result.fences)
    assert "Revenue by month" in result.fences[0].svg
    assert "Revenue by month" not in result.fences[1].svg


def test_a_fence_error_is_a_diagnostic_naming_its_line(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    _page(tmp_path, "Intro.\n\n```charts\ntype: not_a_chart\n```\n")
    project = local_project(tmp_path)

    with ProjectSession.from_project(project) as session:
        result = render_chart_fences(
            session, project.path("docs/post.md").read_board(named=True), False
        )

    assert result.fences == []
    assert result.errors
    assert "line 3" in result.errors[0].message


def test_a_query_that_fails_at_run_time_is_an_error(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    _page(
        tmp_path,
        "```charts\ntype: bar\nquery:\n  columns: [a, b]\n  values:\n"
        "    - [x, 1]\nx: a\ny: missing_column\n```\n",
    )
    project = local_project(tmp_path)

    with ProjectSession.from_project(project) as session:
        result = render_chart_fences(
            session, project.path("docs/post.md").read_board(named=True), False
        )

    assert result.fences == []
    assert result.errors


def test_images_replace_each_fence_and_carry_their_fonts(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    _page(tmp_path, SHARED_QUERY_PAGE)
    project = local_project(tmp_path)

    with ProjectSession.from_project(project) as session:
        result = render_markdown_with_images(
            session, project.path("docs/post.md").read_board(named=True), "post"
        )

    assert result.errors == []
    assert list(result.images) == ["post-1.svg", "post-2.svg"]
    assert "![Revenue by month](post-1.svg)" in result.markdown
    assert "](post-2.svg)" in result.markdown
    assert "The same numbers as a line." in result.markdown
    assert "```charts" not in result.markdown
    for svg in result.images.values():
        assert "data:font/woff2;base64," in svg
        assert "/static/fonts/" not in svg


VARIABLE_PAGE = """\
---
board:
  variables:
    region:
      input: select
      default: US
      options:
        static: [US, EU]
---
```charts
type: bar
title: "Sales in {{ region }}"
query:
  columns: [month, revenue]
  values:
    - [Jan, 100]
x: month
y: revenue
```
"""


def test_variables_reach_each_fence(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    _page(tmp_path, VARIABLE_PAGE)
    project = local_project(tmp_path)

    with ProjectSession.from_project(project) as session:
        page = project.path("docs/post.md").read_board(named=True)
        default = render_chart_fences(session, page, False)
        chosen = render_chart_fences(session, page, False, variables={"region": "EU"})

    assert default.errors == chosen.errors == []
    assert "Sales in US" in default.fences[0].svg
    assert "Sales in EU" in chosen.fences[0].svg


def test_chart_errors_may_be_allowed(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    _page(
        tmp_path,
        "```charts\ntype: bar\nquery:\n  columns: [a, b]\n  values:\n"
        "    - [x, 1]\nx: a\ny: missing_column\n```\n",
    )
    project = local_project(tmp_path)

    with ProjectSession.from_project(project) as session:
        page = project.path("docs/post.md").read_board(named=True)
        result = render_chart_fences(session, page, False, allow_chart_errors=True)

    assert result.errors == []
    assert len(result.fences) == 1
    assert result.warnings


def test_splice_keeps_the_fence_indent() -> None:
    text = "- item\n\n   ```charts\n   a\n   ```\nafter"
    assert splice_lines(text, [(2, 4, "![x](p-1.svg)\n\nmore")]) == (
        "- item\n\n   ![x](p-1.svg)\n\n   more\nafter"
    )
