"""Tests for the new markdown board frontmatter design.

Design:
- Board config lives exclusively under a ``board:`` key in frontmatter.
- All OTHER frontmatter keys are free-form document metadata — never cause
  compile errors.
- ``markdown_to_yaml`` extracts ``fm.get("board", {})`` as board config; the
  remaining keys are document metadata.
- When ``metadata_table=True``, the metadata is prepended as a markdown table
  text row in the generated YAML.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import CompileResult, compile
from dbt_charts.core.compile.compiler import compile_file
from dbt_charts.core.compile.parse.markdown import markdown_to_yaml


class TestMarkdownBoardBlockFrontmatter:
    """Board config under the ``board:`` key."""

    def test_board_block_applies_title(self) -> None:
        md = """\
---
board:
  title: My Report
---

# Body
"""
        data = yaml.safe_load(markdown_to_yaml(md))
        assert data["title"] == "My Report"

    def test_board_block_applies_queries_and_charts(self) -> None:
        md = """\
---
board:
  title: Chart Report
  queries:
    data:
      type: values
      rows:
        - {n: 1}
  charts:
    t:
      query: data
      type: table
---

Body text.
"""
        data = yaml.safe_load(markdown_to_yaml(md))
        assert data["title"] == "Chart Report"
        assert "queries" in data
        assert "charts" in data
        assert "rows" in data

    def test_board_block_typo_still_errors(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A typo inside the board: block must raise — extra='forbid' still applies."""
        from dbt_charts.core.compile.compiler import compile_file

        md = """\
---
board:
  title: Typo Test
  quries:
    data:
      type: values
      rows: []
---

Body.
"""
        p = tmp_path / "charts" / "typo_board.md"
        p.parent.mkdir(parents=True)
        p.write_text(md)
        project = local_project(p.parent)
        result = compile_file(project.path("typo_board.md").read_board())
        assert not result.success
        error_text = " ".join(str(e) for e in result.errors).lower()
        assert (
            "quries" in error_text or "extra" in error_text or "forbidden" in error_text
        )

    def test_pure_prose_no_board_block_compiles(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A .md in charts/ with no board: block and arbitrary frontmatter compiles fine."""
        from dbt_charts.core.compile.compiler import compile_file

        md = """\
---
status: in_progress
owner: alice
milestone: v1
priority: p1
created_at: "2025-01-01"
---

# Task: Do the thing

Some description.
"""
        p = tmp_path / "charts" / "task.md"
        p.parent.mkdir(parents=True)
        p.write_text(md)
        project = local_project(tmp_path)
        result = compile_file(project.path("charts/task.md").read_board())
        assert result.success, f"Compile failed: {result.errors}"
        assert result.board is not None

    def test_task_style_frontmatter_no_extra_field_error(self) -> None:
        """Task-file frontmatter keys (status, owner, etc.) must NOT cause ERR-EXTRA-FIELD."""
        md = """\
---
status: in_progress
owner: alice
milestone: v1
priority: p1
created_at: "2025-01-01"
---

# Task body
"""
        data = yaml.safe_load(markdown_to_yaml(md))
        # Metadata keys must NOT appear at the top level of the generated board dict
        assert "status" not in data
        assert "owner" not in data
        # Body content becomes a text row
        assert "rows" in data
        assert any(
            "Task body" in (row.get("text", "") if isinstance(row, dict) else "")
            for row in data["rows"]
        )

    def test_metadata_captured_separately(self) -> None:
        """parse_markdown_board returns metadata dict with non-board keys."""
        from dbt_charts.core.compile.parse.markdown import parse_markdown_board

        md = """\
---
status: done
owner: bob
board:
  title: My Board
---

Body.
"""
        _yaml_str, metadata = parse_markdown_board(md)
        assert metadata == {"status": "done", "owner": "bob"}

    def test_metadata_empty_when_only_board_key(self) -> None:
        """When frontmatter has only board:, metadata is empty."""
        from dbt_charts.core.compile.parse.markdown import parse_markdown_board

        md = """\
---
board:
  title: Clean Board
---

Body.
"""
        _yaml_str, metadata = parse_markdown_board(md)
        assert metadata == {}

    def test_no_frontmatter_metadata_empty(self) -> None:
        from dbt_charts.core.compile.parse.markdown import parse_markdown_board

        md = "# Just markdown\n"
        _yaml_str, metadata = parse_markdown_board(md)
        assert metadata == {}


class TestMetadataTableRendering:
    """metadata_table=True prepends metadata as a markdown table text row."""

    def test_metadata_table_off_no_table(self) -> None:
        md = """\
---
status: done
owner: alice
---

Body.
"""
        data = yaml.safe_load(markdown_to_yaml(md, metadata_table=False))
        rows = data.get("rows", [])
        text_content = " ".join(
            row.get("text", "") for row in rows if isinstance(row, dict)
        )
        # Should not contain a markdown table
        assert "| status |" not in text_content and "| owner |" not in text_content

    def test_metadata_table_on_prepends_table(self) -> None:
        md = """\
---
status: done
owner: alice
---

Body.
"""
        data = yaml.safe_load(markdown_to_yaml(md, metadata_table=True))
        rows = data.get("rows", [])
        assert rows, "Expected at least one row"
        first_row = rows[0]
        assert isinstance(first_row, dict)
        table_text = first_row.get("text", "")
        # A markdown table for the metadata should be first
        assert "status" in table_text
        assert "done" in table_text
        assert "owner" in table_text
        assert "alice" in table_text

    def test_metadata_table_on_empty_metadata_no_table_prepended(self) -> None:
        """When metadata is empty, metadata_table=True must not prepend anything."""
        md = """\
---
board:
  title: No metadata
---

Body.
"""
        default_data = yaml.safe_load(markdown_to_yaml(md))
        table_data = yaml.safe_load(markdown_to_yaml(md, metadata_table=True))
        # Row count should be the same when there is no metadata
        assert len(default_data.get("rows", [])) == len(table_data.get("rows", []))

    def test_metadata_table_on_no_frontmatter_no_table(self) -> None:
        """No frontmatter → no metadata → metadata_table has nothing to prepend."""
        md = "# Just prose\n\nNo frontmatter.\n"
        data = yaml.safe_load(markdown_to_yaml(md, metadata_table=True))
        rows = data.get("rows", [])
        text_content = " ".join(
            row.get("text", "") for row in rows if isinstance(row, dict)
        )
        assert "|" not in text_content or "Just prose" in text_content

    def test_compile_file_metadata_table_flag(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """compile_file passes metadata_table flag through to markdown_to_yaml."""
        from dbt_charts.core.compile.compiler import compile_file

        md = """\
---
status: in_progress
owner: charlie
---

# Task body
"""
        p = tmp_path / "charts" / "task.md"
        p.parent.mkdir(parents=True)
        p.write_text(md)

        project = local_project(tmp_path)
        board_file = project.path("charts/task.md").read_board()
        result_no_table = compile_file(board_file, markdown_metadata_table=False)
        result_with_table = compile_file(board_file, markdown_metadata_table=True)

        assert result_no_table.success
        assert result_with_table.success

        # The with-table result should have a metadata row as first row
        board_no_table = result_no_table.board
        board_with_table = result_with_table.board
        assert board_no_table is not None
        assert board_with_table is not None


class TestDiscoveryStaysStrict:
    """Auto-discovery must NOT include plain .md files without dct keys outside charts/."""

    def test_plain_md_outside_boards_contributes_no_aliases(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A task-style .md outside charts/ must contribute no aliases even if scanned.

        iter_boards yields .md candidates by suffix; the read_aliases_from_file gate
        (is_markdown_board_content) is where non-board markdown is filtered.
        """
        from dbt_charts.core.serve.alias_index import read_aliases_from_file

        task_md = tmp_path / "workstreams" / "my_task.md"
        task_md.parent.mkdir(parents=True)
        task_md.write_text(
            "---\nstatus: in_progress\naliases:\n  - /old-task/\n---\n# Do the thing\n"
        )
        project = local_project(tmp_path)
        pf = project.path_for_fspath(task_md)
        assert read_aliases_from_file(pf) == [], (
            "Task-style .md outside charts/ must yield no aliases"
        )

    def test_md_with_board_key_outside_boards_is_discovered(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A board: key in frontmatter outside charts/ IS detected and discovered.

        iter_boards enumerates candidates by suffix; is_markdown_board_content
        (consulted by the compiler) gates whether such a file is actually a board.
        The discovery contract is: iter_boards yields the path; the compiler
        decides whether to compile it as a board. For the purposes of this test,
        we verify that the suffix + content twin returns True for the board:
        key case outside charts/.
        """
        from dbt_charts.core.compile.parse.markdown import is_markdown_board_content

        md = tmp_path / "notes" / "report.md"
        md.parent.mkdir(parents=True)
        md.write_text("---\nboard:\n  title: My Report\n---\n# Report\n")
        project = local_project(tmp_path)
        assert project.path("notes/report.md").is_markdown
        assert is_markdown_board_content(
            md.read_text(), in_boards="charts" in md.parts
        ), "board: key in frontmatter outside charts/ must be a board"

    def test_md_in_boards_dir_is_discovered(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:

        boards = tmp_path / "charts"
        boards.mkdir()
        board_md = boards / "report.md"
        board_md.write_text("---\nstatus: done\n---\n# Report\n")
        relpaths = {pf.relpath for pf in local_project(tmp_path).iter_boards()}
        assert "charts/report.md" in relpaths, "Any .md in charts/ must be discovered"

    def test_queries_at_top_level_outside_boards_not_a_board(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """queries: at top level outside charts/ must NOT be detected as a board (regression).

        The flat-form backwards-compat path caused markdown files with top-level
        'queries:' (e.g. a research note listing research queries) to be misread
        as dbt charts boards. After removing the flat form, only 'board:' triggers
        detection outside charts/.
        """
        from dbt_charts.core.compile.parse.markdown import is_markdown_board_content

        p = tmp_path / "notes" / "research.md"
        p.parent.mkdir(parents=True)
        p.write_text(
            "---\nqueries:\n  - what is the revenue\n  - how many users\n---\n# Research\n"
        )
        project = local_project(tmp_path)
        assert project.path("notes/research.md").is_markdown
        assert (
            is_markdown_board_content(p.read_text(), in_boards="charts" in p.parts)
            is False
        ), "queries: at top level outside charts/ must not be detected as a board"

    def test_board_key_outside_boards_is_a_board(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """board: key in frontmatter outside charts/ is detected as a board."""
        from dbt_charts.core.compile.parse.markdown import is_markdown_board_content

        p = tmp_path / "notes" / "report.md"
        p.parent.mkdir(parents=True)
        p.write_text("---\nboard:\n  title: My Report\n---\n# Report\n")
        project = local_project(tmp_path)
        assert project.path("notes/report.md").is_markdown
        assert (
            is_markdown_board_content(p.read_text(), in_boards="charts" in p.parts)
            is True
        ), "board: key in frontmatter must be detected as a board outside charts/"


class TestMetadataKeyCollisionRegression:
    """Regression: document-metadata keys that overlap with board-config names must NOT
    reach the board validator when there is no board: block.

    The flat-form path caused e.g. 'theme: some-research-topic' to reach the theme
    validator and crash on a valid research-note frontmatter key.
    """

    def test_theme_as_document_metadata_not_validated(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """theme: <str> in frontmatter with no board: block must compile fine."""
        from dbt_charts.core.compile.compiler import compile_file

        p = tmp_path / "charts" / "research.md"
        p.parent.mkdir(parents=True)
        p.write_text(
            "---\ntheme: some-research-topic\n---\n# Research note\n\nBody text.\n"
        )
        project = local_project(tmp_path)
        result = compile_file(project.path("charts/research.md").read_board())
        assert result.success, f"Expected success but got errors: {result.errors}"

    def test_variables_list_as_document_metadata_not_validated(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """variables: [x, y] in frontmatter with no board: block must compile fine."""
        from dbt_charts.core.compile.compiler import compile_file

        p = tmp_path / "charts" / "research.md"
        p.parent.mkdir(parents=True)
        p.write_text(
            "---\nvariables:\n  - concept_a\n  - concept_b\n---\n# Research note\n"
        )
        project = local_project(tmp_path)
        result = compile_file(project.path("charts/research.md").read_board())
        assert result.success, f"Expected success but got errors: {result.errors}"

    def test_metadata_keys_do_not_appear_in_board_dict(self) -> None:
        """Top-level frontmatter keys (no board: block) must appear only in metadata,
        never in the board YAML dict."""
        from dbt_charts.core.compile.parse.markdown import parse_markdown_board

        md = """\
---
theme: some-research-topic
variables:
  - concept_a
style: my-style-category
title: Research Note
---

# Body
"""
        yaml_str, metadata = parse_markdown_board(md)
        import yaml as yaml_mod

        board_dict = yaml_mod.safe_load(yaml_str)
        # All these keys must be in metadata, NOT in board_dict
        assert "theme" not in board_dict, (
            f"'theme' leaked into board_dict: {board_dict}"
        )
        assert "variables" not in board_dict, "'variables' leaked into board_dict"
        assert "style" not in board_dict, "'style' leaked into board_dict"
        assert "title" not in board_dict, "'title' leaked into board_dict"
        # They must appear in metadata
        assert metadata["theme"] == "some-research-topic"
        assert metadata["title"] == "Research Note"


class TestDetectCompileAgreement:
    """iter_boards and compile agree on boards under a charts/ directory in the project.

    A markdown file under a ``charts/`` directory is a board by path convention
    (no ``board:`` key required); ``project.iter_boards`` surfaces it and
    ``compile_file`` compiles it without error.
    """

    def test_file_under_boards_dir_compiles_without_board_key(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A .md under the project's charts/ directory compiles without a board: key."""
        from dbt_charts.core.compile.compiler import compile_file
        from dbt_charts.core.compile.parse.markdown import is_markdown_board_content

        project_root = tmp_path / "project"
        (project_root / "charts").mkdir(parents=True)
        md_file = project_root / "charts" / "report.md"
        md_file.write_text("---\nstatus: done\n---\n# Report\n\nBody.\n")

        project = local_project(project_root)
        board_file = project.path_for_fspath(md_file)

        # The suffix + content twin sees it as a board (charts/ in the relpath)
        assert board_file.is_markdown
        assert is_markdown_board_content(md_file.read_text(), in_boards=True), (
            "is_markdown_board_content must return True for a .md under charts/"
        )

        # compile must agree — not reject with ERR_MARKDOWN_NOT_BOARD
        result = compile_file(board_file.read_board())
        assert result.success, (
            f"compile_file rejected a file iter_boards surfaces; errors: {result.errors}"
        )


_LONG_BODY = "\n".join(f"Line {n} of prose." for n in range(20))
_TWO_COLUMNS = "style:\n  text:\n    column:\n      max_number: 2\n"


def _compile_md(
    in_memory_project: Callable[..., FilesystemProject],
    md: str,
    meta: str | None = None,
    extra: dict[str, str] | None = None,
) -> CompileResult:
    files = {"charts/page.md": md, **(extra or {})}
    if meta is not None:
        files["charts/meta.yml"] = meta
    project = in_memory_project(Path("/tmp/test-md-default"), files)
    return compile_file(project.path("charts/page.md").read_board())


def _max_number(result: CompileResult) -> int | None:
    assert result.success and result.board is not None, result.errors
    return result.board.resolved_style.text.column.max_number


class TestMarkdownBoardsDefaultToOneColumn:
    def test_markdown_board_is_one_column(
        self, in_memory_project: Callable[..., FilesystemProject]
    ) -> None:
        assert _max_number(_compile_md(in_memory_project, _LONG_BODY)) == 1

    def test_yaml_board_keeps_the_renderer_choice(self) -> None:
        text = "\n".join(f"  {line}" for line in _LONG_BODY.splitlines())
        result = compile(f"text: |\n{text}\n")
        assert _max_number(result) is None

    def test_frontmatter_overrides_the_default(
        self, in_memory_project: Callable[..., FilesystemProject]
    ) -> None:
        md = f"---\nboard:\n  style:\n    text:\n      column:\n        max_number: 2\n---\n\n{_LONG_BODY}\n"
        assert _max_number(_compile_md(in_memory_project, md)) == 2

    def test_meta_yml_overrides_the_default(
        self, in_memory_project: Callable[..., FilesystemProject]
    ) -> None:
        result = _compile_md(in_memory_project, _LONG_BODY, meta=_TWO_COLUMNS)
        assert _max_number(result) == 2

    def test_extends_template_overrides_the_default(
        self, in_memory_project: Callable[..., FilesystemProject]
    ) -> None:
        md = f"---\nboard:\n  extends: ./base.yml\n---\n\n{_LONG_BODY}\n"
        result = _compile_md(
            in_memory_project, md, extra={"charts/base.yml": _TWO_COLUMNS}
        )
        assert _max_number(result) == 2

    def test_frontmatter_style_siblings_survive_the_default(
        self, in_memory_project: Callable[..., FilesystemProject]
    ) -> None:
        md = f"---\nboard:\n  style:\n    footer:\n      visible: false\n---\n\n{_LONG_BODY}\n"
        result = _compile_md(in_memory_project, md)
        assert _max_number(result) == 1
        assert not result.board.resolved_style.footer.visible

    def test_null_frontmatter_style_compiles(
        self, in_memory_project: Callable[..., FilesystemProject]
    ) -> None:
        md = f"---\nboard:\n  style:\n---\n\n{_LONG_BODY}\n"
        assert _max_number(_compile_md(in_memory_project, md)) == 1

    def test_string_frontmatter_style_is_a_validation_error(
        self, in_memory_project: Callable[..., FilesystemProject]
    ) -> None:
        md = f"---\nboard:\n  style: dark\n---\n\n{_LONG_BODY}\n"
        result = _compile_md(in_memory_project, md)
        assert not result.success
        assert any("style" in str(e).lower() for e in result.errors)

    def test_default_is_not_authored_in_the_parsed_file(self) -> None:
        data = yaml.safe_load(markdown_to_yaml(_LONG_BODY))
        assert "style" not in data
