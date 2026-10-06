"""Tests for board file imports.

Strings ending in .yml/.yaml load as nested boards, and imported boards see the
parent's variables. Import paths may themselves be templated; the imported
content keeps its templates for render-time resolution.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile_file
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.normalize.dispatch import normalize_board
from dbt_charts.core.compile.parse.parser import parse_yaml
from dbt_charts.core.compile.validate.dispatch import validate_board
from dbt_charts.core.execute.adapters import build_adapter_registry


class TestBoardFileImports:
    """Test that .yml/.yaml strings in layout load as nested boards."""

    def test_simple_board_import(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        """Import a simple board file from layout."""
        # Create a child board file
        child_content = """
title: Child Board
text: "This is child content"
"""
        child_file = tmp_path / "child.yml"
        child_file.write_text(child_content)

        # Parent board that imports the child (relative path)
        parent_content = """
title: Parent Board
rows:
  - child.yml
"""
        board = parse_yaml(parent_content)
        errors = validate_board(board)
        assert not errors, f"Validation errors: {errors}"

        compiled = normalize_board(
            board, base_dir=local_project(root=tmp_path).directory()
        )

        # Should have one layout item
        assert len(compiled.layout.items) == 1
        # Item should be a nested board
        item = compiled.layout.items[0]
        assert item.type == "board"
        assert item.board is not None
        assert item.board.title == "Child Board"
        assert item.board.text == "This is child content"

    def test_imported_board_does_not_inherit_html_policy(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        (tmp_path / "child.yml").write_text("title: Child\ntext: hi\n")
        board = parse_yaml(
            "title: Parent\nhtml_policy: trusted-raw\nrows:\n  - child.yml\n"
        )
        compiled = normalize_board(
            board, base_dir=local_project(root=tmp_path).directory()
        )
        assert compiled.html_policy == "trusted-raw"
        nested = compiled.layout.items[0].board
        assert nested is not None
        assert nested.html_policy == "none"

    def test_imported_board_subtree_carries_no_authoring_path(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        """An imported board file's charts are not addressable from the importer.

        Their coordinates mean something else here: `rows.0` in the importing
        file is whatever that file put at `rows.0`, not the imported subtree.
        `_resolve_board_file_import` passes `path_prefix=None` for exactly this,
        and the whole `AuthoringPathPrefix = str | None` type exists to carry the
        distinction — so it needs a test that fails if the suppression is
        reverted to `path_prefix=""`.
        """
        (tmp_path / "partial.yml").write_text(
            """
title: Imported Board
text: Imported prose.
rows:
  - query:
      type: values
      rows:
        - month: Jan
          revenue: 100
    type: bar
    x: month
    y: revenue
"""
        )
        board = parse_yaml(
            """
title: Importing Board
rows:
  - partial.yml
"""
        )
        assert not validate_board(board)

        compiled = normalize_board(
            board, base_dir=local_project(root=tmp_path).directory()
        )

        imported = compiled.layout.items[0].board
        assert imported is not None
        assert imported.layout.items, "expected the imported board to have items"
        assert [item.source_path for item in imported.layout.items] == [""]

    def test_board_import_preserves_variable_templates(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        """Imported board must preserve Jinja templates for interactive variables.

        Variables defined in the parent are interactive (user-facing). Their
        defaults must NOT be baked into the imported file's content at compile
        time — the templates must survive so render-time substitution works.
        """
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.render import render

        child_content = """
title: "Child for {{ model }}"
text: "Processing {{ column }}"
"""
        child_file = tmp_path / "child.yml"
        child_file.write_text(child_content)

        parent_content = """
title: Parent Board
variables:
  model:
    input: text
    default: "my_table"
  column:
    input: text
    default: "sales"
rows:
  - child.yml
"""
        board = parse_yaml(parent_content)
        errors = validate_board(board)
        assert not errors, f"Validation errors: {errors}"

        compiled = normalize_board(
            board, base_dir=local_project(root=tmp_path).directory()
        )

        # Templates must be preserved — NOT resolved to defaults at compile time
        child_board = compiled.layout.items[0].board
        assert "{{ model }}" in child_board.title, (
            f"Title was baked in at compile time: {child_board.title!r}"
        )
        assert "{{ column }}" in child_board.text, (
            f"Content was baked in at compile time: {child_board.text!r}"
        )

        # Render-time substitution must work (default values)
        executor = Executor(
            compiled,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry={},
        )
        svg = render(compiled, executor, format="svg").output
        # Theme default case: title — "Child for my_table" → "Child for My_table"
        assert "Child for My_table" in svg
        assert "Processing sales" in svg

        # And with a different value
        svg_custom = render(
            compiled,
            executor,
            format="svg",
            variables={"model": "orders", "column": "revenue"},
        ).output
        assert "Child for Orders" in svg_custom
        assert "Processing revenue" in svg_custom

    def test_board_import_with_jinja_path(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        """Board import path can use Jinja templates."""
        # Create partials for different types
        (tmp_path / "partials").mkdir()

        numeric_content = """
title: Numeric Chart
text: "Histogram for numeric data"
"""
        (tmp_path / "partials" / "numeric.yml").write_text(numeric_content)

        # Parent board with variable-based path
        parent_content = """
title: Parent Board
variables:
  chart_type:
    input: text
    default: "numeric"
rows:
  - partials/{{ chart_type }}.yml
"""
        board = parse_yaml(parent_content)
        errors = validate_board(board)
        assert not errors, f"Validation errors: {errors}"

        compiled = normalize_board(
            board, base_dir=local_project(root=tmp_path).directory()
        )

        # Should have resolved the path and loaded the partial
        child_board = compiled.layout.items[0].board
        assert child_board.title == "Numeric Chart"

    def test_board_import_not_found_raises_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        """Missing board file should raise CompilationError."""
        parent_content = """
title: Parent Board
rows:
  - nonexistent.yml
"""
        board = parse_yaml(parent_content)

        with pytest.raises(CompilationError) as exc_info:
            normalize_board(board, base_dir=local_project(root=tmp_path).directory())

        assert "not found" in str(exc_info.value).lower()

    @pytest.mark.parametrize(
        ("entry", "why"),
        [
            ("nonexistent.yml", "'charts/nonexistent.yml' does not exist"),
            ("../../../outside.yml", "escape above the project root"),
        ],
    )
    def test_unresolvable_board_import_is_a_reference_diagnostic(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
        entry: str,
        why: str,
    ) -> None:
        """A missing or escaping import is the resolver's ERR-UNRESOLVED-REFERENCE,
        not a bare compile error that reaches the user as ERR-INTERNAL."""
        (tmp_path / "dbt_charts.yml").write_text("")
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "a.yml").write_text(f"title: A\nrows:\n  - {entry}\n")
        project = local_project(root=tmp_path)
        result = compile_file(project.path("charts/a.yml").read_board())
        (error,) = result.errors
        assert error.code == "ERR-UNRESOLVED-REFERENCE"
        assert why in error.message

    def test_board_import_relative_to_base_path(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        """Board imports should be relative to base_path."""
        # Create nested structure
        (tmp_path / "dashboards").mkdir()
        (tmp_path / "partials").mkdir()

        partial_content = """
title: Shared Partial
text: "Reusable content"
"""
        (tmp_path / "partials" / "shared.yml").write_text(partial_content)

        # Dashboard that imports from sibling directory
        dashboard_content = """
title: Dashboard
rows:
  - ../partials/shared.yml
"""
        dashboard_file = tmp_path / "dashboards" / "main.yml"
        dashboard_file.write_text(dashboard_content)

        board = parse_yaml(dashboard_content)
        errors = validate_board(board)
        assert not errors, f"Validation errors: {errors}"

        # Use dashboard directory as base path
        compiled = normalize_board(
            board, base_dir=local_project(root=tmp_path).directory("dashboards")
        )

        child_board = compiled.layout.items[0].board
        assert child_board.title == "Shared Partial"

    def test_two_level_import_anchors_at_each_file_dir(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        """A grandchild import resolves relative to the child file's own directory.

        Pins the base_dir=nested_file.parent contract: when a.yml imports
        partials/b.yml, b.yml's own refs anchor at partials/ (not at a.yml's dir),
        so b.yml can import ../shared/c.yml -> boards/shared/c.yml.
        """
        (tmp_path / "boards" / "partials").mkdir(parents=True)
        (tmp_path / "boards" / "shared").mkdir()

        (tmp_path / "boards" / "shared" / "c.yml").write_text(
            "title: Grandchild\ntext: deep\n"
        )
        # b.yml lives in boards/partials/; its ../shared/c.yml must resolve from there.
        (tmp_path / "boards" / "partials" / "b.yml").write_text(
            "title: Child\nrows:\n  - ../shared/c.yml\n"
        )
        a_content = "title: Parent\nrows:\n  - partials/b.yml\n"

        board = parse_yaml(a_content)
        assert not validate_board(board)

        compiled = normalize_board(
            board, base_dir=local_project(root=tmp_path).directory("boards")
        )

        child = compiled.layout.items[0].board
        assert child.title == "Child"
        grandchild = child.layout.items[0].board
        assert grandchild.title == "Grandchild"
