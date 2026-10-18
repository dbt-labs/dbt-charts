"""Tests for ProjectPath and Project.path() / Project.path_for_fspath().

TDD: written before implementation — must fail until ProjectPath exists.

Proves:
1. ProjectPath is NOT os.PathLike and exposes no filesystem Path.
2. ProjectDirectory.path() rejects absolute refs and upward-escaping refs.
3. A Project subclass whose read_text/exists serve an in-memory dict can
   compile a board (via compile_file) whose layout imports a nested board file
   AND a board with a cross-file query reference — proving full substitution.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.project import Project

# ---------------------------------------------------------------------------
# ProjectPath unit tests
# ---------------------------------------------------------------------------


class TestProjectPathIsNotPathLike:
    def test_no_fspath(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        import os

        p = local_project(tmp_path)
        pf = p.path("charts/dash.yml")
        with pytest.raises(TypeError):
            os.fspath(pf)  # type: ignore[arg-type]


class TestProjectPathProperties:
    def test_relpath(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        p = local_project(tmp_path)
        pf = p.path("charts/dash.yml")
        assert pf.relpath == "charts/dash.yml"


class TestProjectPathExistsAndRead:
    def test_exists_true(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "f.yml").write_text("title: T\n")
        p = local_project(tmp_path)
        assert p.path("f.yml").exists() is True

    def test_exists_false(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        p = local_project(tmp_path)
        assert p.path("missing.yml").exists() is False

    def test_read_text(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "f.yml").write_text("hello")
        p = local_project(tmp_path)
        assert p.path("f.yml").read_text() == "hello"

    def test_read_yaml(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "f.yml").write_text("key: value\n")
        p = local_project(tmp_path)
        assert p.path("f.yml").read_yaml() == {"key": "value"}


class TestProjectDirPath:
    """`ProjectDirectory / ref` resolves a ref relative to the directory."""

    def test_join_same_dir(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        d = local_project(tmp_path).directory("charts")
        assert (d / "other.yml").relpath == "charts/other.yml"

    def test_join_into_subdir(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        d = local_project(tmp_path).directory("charts")
        assert (d / "partials/kpi.yml").relpath == "charts/partials/kpi.yml"

    def test_join_normalizes_parent_dot(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """foo/../bar.yml → bar.yml at the directory level."""
        d = local_project(tmp_path).directory("charts")
        assert (d / "sub/../other.yml").relpath == "charts/other.yml"

    def test_root_dir_resolves_at_root(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """No-argument dir() anchors at the project root."""
        d = local_project(tmp_path).directory()
        assert (d / "dash.yml").relpath == "dash.yml"

    def test_rejects_empty(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        d = local_project(tmp_path).directory("charts")
        with pytest.raises(ValueError, match="empty"):
            _ = d / ""

    def test_rejects_blank(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        d = local_project(tmp_path).directory("charts")
        with pytest.raises(ValueError, match="empty"):
            _ = d / "   "

    def test_rejects_absolute(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        d = local_project(tmp_path).directory("charts")
        with pytest.raises(ValueError, match="must be relative"):
            _ = d / "/etc/passwd"

    def test_rejects_escape_above_root(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        d = local_project(tmp_path).directory("charts")
        with pytest.raises(ValueError, match="escape"):
            _ = d / "../../etc/passwd"

    def test_reads_through_project(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The resolved path reads through the directory's Project."""
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "other.yml").write_text("k: v\n")
        resolved = local_project(tmp_path).directory("charts") / "other.yml"
        assert resolved.read_yaml() == {"k": "v"}

    def test_result_is_not_path_like(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        import os

        d = local_project(tmp_path).directory("charts")
        with pytest.raises(TypeError):
            os.fspath(d / "dash.yml")  # type: ignore[arg-type]


class TestProjectPathParent:
    """ProjectPath.parent is the directory handle refs resolve against.

    A file has no `/` (`dir / child` is idiomatic; `file / x` is not); sibling
    resolution is the explicit `pf.parent / ref`.
    """

    def test_parent_resolves_sibling(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        base = local_project(tmp_path).path("charts/dash.yml")
        assert (base.parent / "other.yml").relpath == "charts/other.yml"

    def test_parent_of_root_path(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        base = local_project(tmp_path).path("dash.yml")
        assert (base.parent / "other.yml").relpath == "other.yml"


class TestProjectPathForFspath:
    def test_path_for_fspath_roundtrip(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        p = local_project(tmp_path)
        abs_path = tmp_path / "charts" / "dash.yml"
        pf = p.path_for_fspath(abs_path)
        assert pf.relpath == "charts/dash.yml"

    def test_path_for_fspath_outside_root_raises(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        p = local_project(tmp_path)
        outside = tmp_path.parent / "outside.yml"
        with pytest.raises(ValueError, match="outside"):
            p.path_for_fspath(outside)


# ---------------------------------------------------------------------------
# In-memory substitution integration test
# ---------------------------------------------------------------------------


class TestInMemorySubstitutionViaCompileFile:
    """compile_file must route ALL board/sub-file reads through the Project."""

    def test_nested_board_import_reads_from_memory(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """A board whose layout imports file: nested.yml — must come from memory."""
        nested_yaml = """\
title: Nested Board
text: |
  Loaded from memory.
"""
        main_yaml = """\
title: Main Board
rows:
  - nested.yml
"""
        files: dict[str, Any] = {
            "charts/main.yml": main_yaml,
            "charts/nested.yml": nested_yaml,
        }

        # Write main.yml to disk as a negative sentinel — InMemoryProject.exists()
        # checks the in-memory dict, so the disk file is irrelevant to existence.
        # "THIS SHOULD NOT BE READ" proves body reads come from the in-memory dict.
        # do NOT write nested.yml — it must come from the in-memory dict.
        boards_dir = tmp_path / "charts"
        boards_dir.mkdir()
        (boards_dir / "main.yml").write_text("THIS SHOULD NOT BE READ")

        project = in_memory_project(tmp_path, files)

        from dbt_charts.core.compile.compiler import compile_file

        result = compile_file(project.path("charts/main.yml").read_board())
        assert result.success, result.errors
        # The nested board was loaded from memory (contains "Loaded from memory.")
        board = result.board
        assert board is not None
        # Walk layout to find the nested board
        nested_boards = [
            item.board
            for item in board.layout.items
            if item.type == "board" and item.board is not None
        ]
        assert nested_boards, "Expected at least one nested board in layout"
        assert "Loaded from memory" in nested_boards[0].text

    def test_cross_file_query_ref_reads_from_memory(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """A board with queries.ref = file.queries.name — sub-file from memory."""
        shared_yaml = """\
queries:
  shared_q:
    type: values
    rows:
      - val: 1
"""
        main_yaml = """\
title: Cross-ref Board
queries:
  my_q: shared.queries.shared_q
charts:
  c:
    query: my_q
    type: kpi
    value: val
rows:
  - c
"""
        files: dict[str, Any] = {
            "charts/main.yml": main_yaml,
            "charts/shared.yml": shared_yaml,
        }

        boards_dir = tmp_path / "charts"
        boards_dir.mkdir()
        # Write main.yml to disk as a negative sentinel — InMemoryProject.exists()
        # checks the in-memory dict; existence routes through the project seam.
        # Body reads must come from the in-memory dict, not from disk.
        (boards_dir / "main.yml").write_text("THIS SHOULD NOT BE READ")

        project = in_memory_project(tmp_path, files)

        from dbt_charts.core.compile.compiler import compile_file

        result = compile_file(project.path("charts/main.yml").read_board())
        assert result.success, result.errors
        assert "my_q" in result.query_registry

    def test_cross_file_chart_ref_reads_from_memory(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """A board with charts.c: shared.charts.kpi — shared.yml from memory only."""
        shared_yaml = """\
queries:
  sq:
    type: values
    rows:
      - val: 7
charts:
  kpi:
    query: sq
    type: kpi
    value: val
"""
        main_yaml = """\
title: Chart Ref Board
queries:
  sq:
    type: values
    rows:
      - val: 7
charts:
  c: shared.charts.kpi
rows:
  - c
"""
        files: dict[str, Any] = {
            "charts/main.yml": main_yaml,
            "charts/shared.yml": shared_yaml,
        }

        boards_dir = tmp_path / "charts"
        boards_dir.mkdir()
        # Write main.yml to disk as a negative sentinel — InMemoryProject.exists() checks
        # the in-memory dict; existence routes through the project seam, not the disk.
        # shared.yml is NOT on disk — it must be served from the in-memory dict.
        (boards_dir / "main.yml").write_text("THIS SHOULD NOT BE READ")

        project = in_memory_project(tmp_path, files)

        from dbt_charts.core.compile.compiler import compile_file

        result = compile_file(project.path("charts/main.yml").read_board())
        assert result.success, result.errors
        assert result.board is not None

    def test_cross_file_variable_ref_reads_from_memory(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """A board with variables.my_var: other.variables.my_var — other.yml from memory."""
        other_yaml = """\
variables:
  my_var:
    default: hello
"""
        main_yaml = """\
title: Variable Ref Board
variables:
  my_var: other.variables.my_var
rows: []
"""
        files: dict[str, Any] = {
            "charts/main.yml": main_yaml,
            "charts/other.yml": other_yaml,
        }

        boards_dir = tmp_path / "charts"
        boards_dir.mkdir()
        # Write main.yml to disk as a negative sentinel — InMemoryProject.exists() checks
        # the in-memory dict; existence routes through the project seam, not the disk.
        # other.yml is NOT on disk — it must be served from the in-memory dict.
        (boards_dir / "main.yml").write_text("THIS SHOULD NOT BE READ")

        project = in_memory_project(tmp_path, files)

        from dbt_charts.core.compile.compiler import compile_file

        result = compile_file(project.path("charts/main.yml").read_board())
        assert result.success, result.errors
        assert "my_var" in (result.board.variables if result.board else {})


class TestIterBoardsValidateAll:
    """validate_paths must enumerate boards through Project.iter_boards, not raw disk."""

    def test_validate_all_uses_iter_boards_not_disk(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """In-memory boards are validated; a decoy on-disk file is not picked up."""
        board_a = """\
title: Board A
queries:
  q:
    type: values
    rows:
      - val: 1
charts:
  c:
    query: q
    type: kpi
    value: val
rows:
  - c
"""
        board_b = """\
title: Board B
queries:
  q:
    type: values
    rows:
      - val: 2
charts:
  c:
    query: q
    type: kpi
    value: val
rows:
  - c
"""
        files: dict[str, str] = {
            "charts/a.yml": board_a,
            "charts/sub/b.yml": board_b,
        }

        # Write a decoy on-disk file that is NOT in the in-memory dict.
        # If discovery uses the raw filesystem glob this file will be picked up;
        # if it routes through iter_boards it will not appear in results.
        boards_dir = tmp_path / "charts"
        boards_dir.mkdir()
        (boards_dir / "zzz_ondisk.yml").write_text("title: Decoy\nrows: []\n")

        project = in_memory_project(tmp_path, files)

        from dbt_charts.agent_api.validate import validate_paths

        results = validate_paths(None, project=project)

        result_paths = {r.path for r in results}
        # Both in-memory boards must appear
        assert any("a.yml" in str(p) for p in result_paths), result_paths
        assert any("b.yml" in str(p) for p in result_paths), result_paths
        # The decoy on-disk file must NOT appear
        assert not any("zzz_ondisk" in str(p) for p in result_paths), result_paths
        # Exactly the two in-memory boards
        assert len(results) == 2, results

    def test_iter_boards_under_dot_yields_whole_project(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """under="." enumerates every relpath, not just a named subtree.

        This is the scan-root layout (alias_index uses under="." when boards are
        not under charts/). A reference substitution backend must honor it.
        """
        files: dict[str, str] = {
            "charts/a.yml": "title: A\n",
            "report.md": "---\nboard:\n  title: R\n---\n",
        }
        project = in_memory_project(tmp_path, files)

        relpaths = {pf.relpath for pf in project.iter_boards(under=".")}
        assert relpaths == {"charts/a.yml", "report.md"}, relpaths


class TestListDashboardsSubstitution:
    """list_boards and search_files must enumerate through Project.iter_boards."""

    def test_list_boards_uses_iter_boards_not_disk(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """In-memory boards are listed; on-disk decoy is invisible."""
        board_yaml = (
            "title: In-Memory Board\n"
            "queries:\n  q:\n    sql: SELECT 1\n    source: test\n"
            "charts:\n  c:\n    query: q\n    type: kpi\n    value: q\n"
            "rows:\n  - c\n"
        )
        files = {"charts/inmem.yml": board_yaml}
        # Decoy on disk — must NOT appear in results
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "decoy.yml").write_text(
            "title: Decoy\nqueries:\n  q:\n    sql: SELECT 2\n    source: test\nrows: []\n"
        )

        project = in_memory_project(tmp_path, files)

        from dbt_charts.agent_api.boards import list_boards

        result = list_boards(project, under=".")
        titles = [d.title for d in result.boards]
        assert "In-Memory Board" in titles, titles
        assert "Decoy" not in titles, titles
        assert result.directory.relpath == "."
        assert not result.directory.relpath.startswith("/")

    def test_search_files_uses_iter_boards_not_disk(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """search_files finds in-memory board; on-disk decoy is invisible."""
        board_yaml = (
            "title: Xyzzy Sales\n"
            "notes: Xyzzy revenue metrics\n"
            "queries:\n  q:\n    sql: SELECT 1\n    source: test\n"
            "charts:\n  c:\n    query: q\n    type: kpi\n    value: q\n"
            "rows:\n  - c\n"
        )
        files = {"charts/xyzzy.yml": board_yaml}
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "decoy.yml").write_text(
            "title: Decoy Xyzzy\nnotes: should not appear\nrows: []\n"
        )

        project = in_memory_project(tmp_path, files)

        from dbt_charts.agent_api.search import search_files

        result = search_files(project, text="xyzzy")
        titles = [h.title for h in result.results]
        assert "Xyzzy Sales" in titles, titles
        assert "Decoy Xyzzy" not in titles, titles


class TestEscapingRefCompileError:
    """Escaping refs must surface as CompilationError in CompileResult, not raise."""

    def test_escaping_board_import_returns_compile_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """rows: ['../../escape.yml'] must yield CompileResult.errors, not ValueError."""
        main_yaml = """\
title: Escape Test
rows:
  - ../../escape.yml
"""
        boards_dir = tmp_path / "charts"
        boards_dir.mkdir()
        (boards_dir / "main.yml").write_text(main_yaml)

        from dbt_charts.core.compile.compiler import compile_file

        project = local_project(tmp_path)
        result = compile_file(project.path("charts/main.yml").read_board())

        assert not result.success
        assert result.errors
        # Must be a structured error, not a raw exception propagating
        assert any(
            "escape" in str(e).lower() or "outside" in str(e).lower()
            for e in result.errors
        )
