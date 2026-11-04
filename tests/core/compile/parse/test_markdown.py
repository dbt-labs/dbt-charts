"""Tests for markdown report file loading and transformation."""

from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.parse.markdown import (
    ChartFence,
    chart_fences,
    is_markdown_board_content,
    markdown_to_yaml,
    parse_markdown_board,
)


def _rows(md: str) -> list[object]:
    return yaml.safe_load(markdown_to_yaml(md))["rows"]


_BAR_FENCE = """\
```charts
type: bar
query: revenue
x: month
y: total
```
"""


class TestChartFence:
    """A ``charts`` fence holds one ``rows:`` item, and prose stays prose."""

    def test_prose_is_one_text_row(self) -> None:
        assert _rows("# Hello\n\nSome text.\n") == [{"text": "# Hello\n\nSome text."}]

    def test_a_chart_splits_the_prose_around_it(self) -> None:
        assert _rows(f"Intro.\n\n{_BAR_FENCE}\nOutro.\n") == [
            {"text": "Intro."},
            {"type": "bar", "query": "revenue", "x": "month", "y": "total"},
            {"text": "Outro."},
        ]

    def test_a_chart_may_open_or_close_the_file(self) -> None:
        chart = {"type": "bar", "query": "revenue", "x": "month", "y": "total"}
        assert _rows(_BAR_FENCE) == [chart]
        assert _rows(f"{_BAR_FENCE}\nAfter.\n") == [chart, {"text": "After."}]
        assert _rows(f"Before.\n\n{_BAR_FENCE}") == [{"text": "Before."}, chart]

    def test_a_bare_name_places_a_chart_from_the_frontmatter(self) -> None:
        assert _rows("```charts\nrevenue\n```\n") == ["revenue"]

    def test_a_file_path_places_a_board_file(self) -> None:
        assert _rows("```charts\n./revenue.yml\n```\n") == ["./revenue.yml"]

    def test_a_whole_board_keeps_its_authored_order(self) -> None:
        md = """\
```charts
charts:
  zeta:
    type: bar
  alpha:
    type: line
```
"""
        (board,) = _rows(md)
        assert isinstance(board, dict)
        assert list(board["charts"]) == ["zeta", "alpha"]

    @pytest.mark.parametrize(
        "fence",
        [
            "~~~charts\nrevenue\n~~~\n",
            "````charts\nrevenue\n````\n",
            "``` charts\nrevenue\n```\n",
            "   ```charts\n   revenue\n   ```\n",
        ],
    )
    def test_every_markdown_fence_spelling_counts(self, fence: str) -> None:
        assert _rows(fence) == ["revenue"]

    @pytest.mark.parametrize(
        "body",
        [
            "```yaml\ntype: bar\n```\n",
            "```charts-example\ntype: bar\n```\n",
            "```\ntype: bar\n```\n",
            "```\ncharts\n```\n",
            "```\ncharts --help\n```\n",
            "````markdown\n```charts\nrevenue\n```\n````\n",
            "Example:\n\n    ```charts\n    revenue\n    ```\n",
            "```charts\nrevenue\n",
            "```dbt-charts\nrevenue\n```\n",
            "```chart\nrevenue\n```\n",
        ],
        ids=[
            "yaml",
            "example",
            "plain",
            "plain-naming-the-language",
            "plain-naming-a-command",
            "shown-in-longer",
            "indented",
            "unclosed",
            "old-name",
            "singular",
        ],
    )
    def test_everything_else_stays_in_the_prose(self, body: str) -> None:
        assert _rows(f"Before.\n\n{body}\nAfter.\n") == [
            {"text": f"Before.\n\n{body}\nAfter."}
        ]

    def test_a_document_keeps_the_jinja_in_its_charts(self) -> None:
        sql = "select 1 where {{ filter('region') }}"
        md = f"Print {{{{ this }}}}.\n\n```charts\ntype: bar\nquery:\n  sql: {sql}\n```\n"
        (prose, chart) = _rows(md)
        assert prose == {"text": 'Print {{ "{{" }} this }}.'}
        assert chart == {"type": "bar", "query": {"sql": sql}}

    def test_a_retired_chart_line_is_prose_in_a_document(self) -> None:
        assert _rows("{{ chart revenue }}\n") == [
            {"text": '{{ "{{" }} chart revenue }}'}
        ]

    def test_options_are_refused(self) -> None:
        with pytest.raises(ValueError, match="takes no options"):
            markdown_to_yaml("```charts {file=charts/a.yml}\n```\n")

    def test_an_empty_fence_is_refused(self) -> None:
        with pytest.raises(ValueError, match="is empty"):
            markdown_to_yaml("```charts\n```\n")

    def test_a_list_is_refused(self) -> None:
        with pytest.raises(ValueError, match="one chart, board or file"):
            markdown_to_yaml("```charts\n- a\n- b\n```\n")

    def test_a_yaml_error_names_the_line_of_the_file(self) -> None:
        md = "---\ntitle: T\n---\n\nProse.\n\n```charts\ntype: bar\nx: [\n```\n"
        with pytest.raises(yaml.YAMLError) as caught:
            markdown_to_yaml(md)
        assert isinstance(caught.value, yaml.MarkedYAMLError)
        assert caught.value.problem_mark is not None
        assert caught.value.problem_mark.line + 1 == 10


class TestMarkdownToYaml:
    """Tests for transforming a markdown board file into YAML for the compiler."""

    def test_simple_report(self):
        md = """\
---
board:
  title: Report
  queries:
    revenue:
      sql: SELECT 1 as n
      type: sql
  charts:
    rev_line:
      type: line
      query: revenue
      x: n
      y: n
---

# Report

Revenue improved.

```charts
rev_line
```

More narrative.
"""
        yaml_str = markdown_to_yaml(md)
        data = yaml.safe_load(yaml_str)
        assert data["title"] == "Report"
        assert "queries" in data
        assert "charts" in data
        # Should produce rows layout with content and chart ref
        assert "rows" in data
        rows = data["rows"]
        assert len(rows) == 3
        # First row: content block
        assert isinstance(rows[0], dict) and "text" in rows[0]
        assert "# Report" in rows[0]["text"]
        # Second row: chart reference
        assert rows[1] == "rev_line"
        # Third row: content block
        assert isinstance(rows[2], dict) and "text" in rows[2]
        assert "More narrative" in rows[2]["text"]

    def test_content_only_report(self):
        """Markdown with no embeds and a board: title produces a single content row."""
        md = """\
---
board:
  title: Notes
---

# Just text

No charts here.
"""
        yaml_str = markdown_to_yaml(md)
        data = yaml.safe_load(yaml_str)
        assert data["title"] == "Notes"
        assert "rows" in data
        assert len(data["rows"]) == 1
        assert "# Just text" in data["rows"][0]["text"]

    def test_missing_frontmatter_treats_entire_file_as_body(self):
        md = "# No frontmatter\n\nJust markdown.\n"
        yaml_str = markdown_to_yaml(md)
        data = yaml.safe_load(yaml_str)
        assert data["rows"] == [{"text": "# No frontmatter\n\nJust markdown."}]

    def test_empty_frontmatter_treats_body_as_content(self):
        md = "---\n---\n\n# Empty\n"
        yaml_str = markdown_to_yaml(md)
        data = yaml.safe_load(yaml_str)
        assert data["rows"] == [{"text": "# Empty"}]

    def test_preserves_variables(self):
        md = """\
---
board:
  title: Vars
  variables:
    region:
      input: select
      default: NA
      options: ["NA", "EMEA"]
---

Hello {{ region }}.
"""
        yaml_str = markdown_to_yaml(md)
        data = yaml.safe_load(yaml_str)
        assert "variables" in data
        assert data["variables"]["region"]["input"] == "select"

    def test_preserves_style_and_theme(self):
        md = """\
---
board:
  title: Styled
  theme: neon
  style:
    background: "#000"
---

Content.
"""
        yaml_str = markdown_to_yaml(md)
        data = yaml.safe_load(yaml_str)
        assert data["theme"] == "neon"
        assert data["style"]["background"] == "#000"


class TestIsMarkdownBoard:
    """Tests for detecting whether a .md file is a dbt charts markdown report,
    via the ``ProjectPath.is_markdown`` (suffix) + ``is_markdown_board_content``
    (content) twin the compiler uses."""

    def test_md_with_dbt_charts_frontmatter(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        p = tmp_path / "charts" / "report.md"
        p.parent.mkdir(parents=True)
        p.write_text("---\ntitle: R\ncharts:\n  c:\n    type: kpi\n---\n# Hi\n")
        project = local_project(tmp_path)
        assert project.path("charts/report.md").is_markdown is True
        assert (
            is_markdown_board_content(p.read_text(), in_boards=True, named=False)
            is True
        )

    def test_md_in_boards_dir(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        p = tmp_path / "charts" / "report.md"
        p.parent.mkdir(parents=True)
        p.write_text("---\ntitle: R\n---\n# Hi\n")
        project = local_project(tmp_path)
        assert project.path("charts/report.md").is_markdown is True
        assert (
            is_markdown_board_content(p.read_text(), in_boards=True, named=False)
            is True
        )

    def test_md_in_boards_dir_without_frontmatter(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        p = tmp_path / "charts" / "report.md"
        p.parent.mkdir(parents=True)
        p.write_text("# Just markdown\n")
        project = local_project(tmp_path)
        assert project.path("charts/report.md").is_markdown is True
        assert (
            is_markdown_board_content(p.read_text(), in_boards=True, named=False)
            is True
        )

    def test_md_in_boards_dir_with_empty_frontmatter(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        p = tmp_path / "charts" / "report.md"
        p.parent.mkdir(parents=True)
        p.write_text("---\n---\n\n# Just markdown\n")
        project = local_project(tmp_path)
        assert project.path("charts/report.md").is_markdown is True
        assert (
            is_markdown_board_content(p.read_text(), in_boards=True, named=False)
            is True
        )

    def test_md_without_frontmatter_outside_boards(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        p = tmp_path / "report.md"
        p.write_text("# Just a readme\n\nNo frontmatter.\n")
        project = local_project(tmp_path)
        assert project.path("report.md").is_markdown is True
        assert (
            is_markdown_board_content(p.read_text(), in_boards=False, named=False)
            is False
        )

    def test_non_md_file(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        p = tmp_path / "data.yml"
        p.write_text("---\ntitle: R\n---\n")
        project = local_project(tmp_path)
        assert project.path("data.yml").is_markdown is False

    def test_md_with_board_key_outside_boards(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        """A .md file with a top-level board: key is detected as a board outside charts/."""
        p = tmp_path / "report.md"
        p.write_text("---\nboard:\n  title: My Report\n---\n# Report\n")
        project = local_project(tmp_path)
        assert project.path("report.md").is_markdown is True
        assert (
            is_markdown_board_content(p.read_text(), in_boards=False, named=False)
            is True
        )

    def test_md_with_queries_only_outside_boards_not_detected(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        """A .md file with queries: at top level but no board: is NOT detected outside charts/."""
        p = tmp_path / "research.md"
        p.write_text("---\nqueries:\n  - what is the revenue\n---\n# Research\n")
        project = local_project(tmp_path)
        assert project.path("research.md").is_markdown is True
        assert (
            is_markdown_board_content(p.read_text(), in_boards=False, named=False)
            is False
        )

    def test_plain_md_outside_boards(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        """A plain .md file without dbt charts keys outside charts/ is not detected."""
        p = tmp_path / "README.md"
        p.write_text("---\nauthor: me\n---\n# README\n")
        project = local_project(tmp_path)
        assert project.path("README.md").is_markdown is True
        assert (
            is_markdown_board_content(p.read_text(), in_boards=False, named=False)
            is False
        )

    def test_project_path_is_markdown_is_lexical_no_io(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """ProjectPath.is_markdown is a pure suffix check: relpaths that never
        exist on disk must still resolve correctly (no I/O)."""
        project = local_project(tmp_path)
        assert project.path("reports/summary.md").is_markdown is True
        assert project.path("nested/dir/notes.markdown").is_markdown is True
        assert project.path("data/table.csv").is_markdown is False
        assert project.path("README.MD").is_markdown is True


class TestCompileFileMarkdown:
    """Integration: compile_file() with .md board files."""

    def test_compile_md_board(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        from dbt_charts.core.compile.compiler import compile_file

        md = """\
---
board:
  title: MD Report
  source: warehouse
  queries:
    data:
      sql: SELECT 1 as n
      type: sql
  charts:
    kpi:
      type: kpi
      query: data
      value: n
---

# Report Title

Intro text.

```charts
kpi
```

Closing.
"""
        p = tmp_path / "charts" / "report.md"
        p.parent.mkdir(parents=True)
        p.write_text(md)

        project = local_project(tmp_path)
        result = compile_file(project.path("charts/report.md").read_board())
        assert result.success, f"Errors: {result.errors}"
        assert result.board is not None
        assert result.board.title == "MD Report"
        # Should have queries and charts in registries
        assert "data" in result.query_registry

    def test_compile_fences_that_share_a_query_in_either_order(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Names are shared across the file, so no frontmatter is needed."""
        from dbt_charts.core.compile.compiler import compile_file

        md = """\
```charts
type: line
query: q
x: m
y: n
```

Between them.

```charts
queries:
  q:
    columns: [m, n]
    values:
      - [Jan, 1]
charts:
  c:
    type: bar
    query: q
    x: m
    y: n
```
"""
        p = tmp_path / "charts" / "notes.md"
        p.parent.mkdir(parents=True)
        p.write_text(md)

        project = local_project(tmp_path)
        result = compile_file(project.path("charts/notes.md").read_board())

        assert result.success, f"Errors: {result.errors}"
        assert result.board is not None
        assert [item.type for item in result.board.layout.items] == [
            "chart",
            "board",
            "board",
        ]

    def test_compile_a_chart_fence_over_a_frontmatter_query(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.compile.compiler import compile_file

        md = """\
---
board:
  queries:
    q:
      columns: [m, n]
      values:
        - [Jan, 1]
---

```charts
type: bar
query: q
x: m
y: n
```
"""
        p = tmp_path / "charts" / "report.md"
        p.parent.mkdir(parents=True)
        p.write_text(md)

        project = local_project(tmp_path)
        result = compile_file(project.path("charts/report.md").read_board())

        assert result.success, f"Errors: {result.errors}"
        assert result.board is not None
        assert [item.type for item in result.board.layout.items] == ["chart"]

    def test_compile_md_missing_frontmatter(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        from dbt_charts.core.compile.compiler import compile_file

        p = tmp_path / "charts" / "report.md"
        p.parent.mkdir(parents=True)
        p.write_text("# No frontmatter\n")

        project = local_project(tmp_path)
        result = compile_file(project.path("charts/report.md").read_board())
        assert result.success, f"Errors: {result.errors}"
        assert result.board is not None

    def test_compile_md_empty_frontmatter(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        from dbt_charts.core.compile.compiler import compile_file

        p = tmp_path / "charts" / "report.md"
        p.parent.mkdir(parents=True)
        p.write_text("---\n---\n\n# Empty frontmatter\n")

        project = local_project(tmp_path)
        result = compile_file(project.path("charts/report.md").read_board())
        assert result.success, f"Errors: {result.errors}"
        assert result.board is not None

    def test_iter_boards_finds_md_and_yml_in_boards_dir(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:

        boards_dir = tmp_path / "charts"
        boards_dir.mkdir()
        (boards_dir / "report.md").write_text(
            "---\ntitle: R\ncharts:\n  c:\n    type: kpi\n---\n# Hi\n"
        )
        (boards_dir / "dashboard.yml").write_text(
            "queries:\n  q:\n    sql: SELECT 1\n    type: sql\nrows:\n  - q\n"
        )

        relpaths = {pf.relpath for pf in local_project(tmp_path).iter_boards()}
        assert "charts/report.md" in relpaths
        assert "charts/dashboard.yml" in relpaths

    def test_is_markdown_board_true_under_boards(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        p = tmp_path / "charts" / "report.md"
        p.parent.mkdir(parents=True)
        p.write_text("---\ntitle: R\n---\n# Hi\n")
        project = local_project(tmp_path)
        assert project.path("charts/report.md").is_markdown is True
        assert (
            is_markdown_board_content(p.read_text(), in_boards=True, named=False)
            is True
        )

    def test_is_markdown_board_false_for_plain_readme(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        p = tmp_path / "README.md"
        p.write_text("# Just a readme\n")
        project = local_project(tmp_path)
        assert project.path("README.md").is_markdown is True
        assert (
            is_markdown_board_content(p.read_text(), in_boards=False, named=False)
            is False
        )

    def test_plain_md_compile_error_is_not_board_file(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        """Plain .md (no frontmatter) must error with 'not a dbt charts board file', not 'missing opening ---'.

        Regression: compiler called markdown_to_yaml unconditionally on all .md files,
        exposing an internal parse error to users who pass a README.md.
        """
        from dbt_charts.core.compile.compiler import compile_file

        p = tmp_path / "README.md"
        p.write_text("# README\n\nJust docs.\n")

        project = local_project(tmp_path)
        result = compile_file(project.path("README.md").read_board())
        assert not result.success
        error_text = " ".join(str(e) for e in result.errors).lower()
        assert "not a dbt charts board file" in error_text, (
            f"Expected 'not a dbt charts board file' but got: {result.errors}"
        )
        # Must NOT bubble up the internal parse error
        assert "missing opening ---" not in error_text

    def test_plain_md_with_frontmatter_outside_boards_is_not_board(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        """A plain .md with frontmatter but no dbt charts keys outside charts/ is not a board."""
        from dbt_charts.core.compile.compiler import compile_file

        p = tmp_path / "notes.md"
        p.write_text("---\nauthor: me\ntitle: Notes\n---\n# Notes\n")

        project = local_project(tmp_path)
        result = compile_file(project.path("notes.md").read_board())
        assert not result.success
        error_text = " ".join(str(e) for e in result.errors).lower()
        assert "not a dbt charts board file" in error_text


class TestBoardApiMarkdownGuard:
    """Regression: rendering a plain .md board must raise ValueError with clear message."""

    def test_plain_md_raises_clear_valueerror(self, tmp_path: Path):
        """Rendering a plain .md must raise ValueError mentioning 'not a dbt charts board file'."""

        from ..._svg_render import render_board_file

        p = tmp_path / "README.md"
        p.write_text("# README\n\nNot a board.\n")

        with pytest.raises(ValueError, match="(?i)not a dbt charts board file"):
            render_board_file(p)


_JINJA_PROSE = (
    "# dbt notes\n\n"
    "Call `{{ ref('orders') }}` or `{{ source('raw', 'orders') }}`.\n\n"
    "{% if x %}block{% endif %} and {# a comment #} and {{{ three }}}.\n\n"
    "```sql\nselect * from {{ ref('orders') }}\n```\n"
)


def _text_of(yaml_str: str) -> str:
    rows = yaml.safe_load(yaml_str)["rows"]
    return "\n\n".join(row["text"] for row in rows)


class TestDocumentsAreNotTemplates:
    """A markdown file with no top-level ``board:`` is a document: its Jinja
    tokens are prose and print literally."""

    def _printed(self, yaml_str: str) -> str:
        from dbt_charts.core.compile.template.display_text import (
            resolve_display_text,
        )

        return resolve_display_text(_text_of(yaml_str), {}, {}, None)

    def test_tokens_in_prose_and_inline_code_print_literally(self) -> None:
        assert self._printed(markdown_to_yaml(_JINJA_PROSE)) == _JINJA_PROSE.strip()

    def test_fenced_code_is_left_as_written(self) -> None:
        text = _text_of(markdown_to_yaml(_JINJA_PROSE))
        assert "```sql\nselect * from {{ ref('orders') }}\n```" in text

    def test_a_board_key_keeps_the_body_a_template(self) -> None:
        md = "---\nboard:\n  title: T\n---\n\nHello {{ region }}.\n"
        assert _text_of(markdown_to_yaml(md)) == "Hello {{ region }}."

    def test_frontmatter_is_not_escaped(self) -> None:
        md = "---\nowner: '{{ me }}'\n---\n\nBody.\n"
        _, metadata = parse_markdown_board(md)
        assert metadata == {"owner": "{{ me }}"}

    def test_metadata_table_prints_template_looking_values_literally(self) -> None:
        md = "---\nowner: '{{ me }}'\n---\n\nBody.\n"
        yaml_str = markdown_to_yaml(md, metadata_table=True)
        assert "| owner | {{ me }} |" in self._printed(yaml_str)


class TestChartFences:
    """Where each chart fence sits in the file and in the board's ``rows:``."""

    def test_spans_and_rows_count_the_prose_between(self) -> None:
        md = f"---\ntitle: T\n---\nIntro.\n\n{_BAR_FENCE}\nMiddle.\n\n{_BAR_FENCE}"

        assert chart_fences(md) == [
            ChartFence(first_line=5, last_line=10, row=1),
            ChartFence(first_line=14, last_line=19, row=3),
        ]

    def test_rows_match_the_compiled_rows(self) -> None:
        md = f"{_BAR_FENCE}\nBetween.\n\n{_BAR_FENCE}\nAfter.\n"
        rows = _rows(md)

        assert [rows[f.row] for f in chart_fences(md)] == [
            {"type": "bar", "query": "revenue", "x": "month", "y": "total"}
        ] * 2

    def test_a_fence_shown_inside_a_longer_one_is_not_a_chart(self) -> None:
        assert chart_fences(f"````markdown\n{_BAR_FENCE}````\n") == []


class TestNamedMarkdownBoard:
    """A file named directly is a board when it holds a chart fence; a listing
    keeps the frontmatter rule."""

    def test_named_with_a_chart_fence_is_a_board(self) -> None:
        assert is_markdown_board_content(_BAR_FENCE, in_boards=False, named=True)

    def test_named_without_a_chart_fence_is_not(self) -> None:
        text = "# Notes\n\n```yaml\ntype: bar\n```\n"
        assert not is_markdown_board_content(text, in_boards=False, named=True)

    def test_a_listed_file_with_a_chart_fence_is_not(self) -> None:
        assert not is_markdown_board_content(_BAR_FENCE, in_boards=False, named=False)
