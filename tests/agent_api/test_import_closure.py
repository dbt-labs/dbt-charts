"""Tests for board_import_closure — transitive import walker."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.agent_api.import_closure import board_import_closure
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.import_refs import ImportRefs


@pytest.fixture
def project(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> FilesystemProject:
    (tmp_path / "charts").mkdir()
    return local_project(tmp_path)


def write_board(project: FilesystemProject, relpath: str, content: str) -> None:
    full = project.root / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")


class TestNoImports:
    def test_returns_board_itself(self, project: FilesystemProject) -> None:
        write_board(project, "charts/a.yml", "title: A\n")
        path = project.path("charts/a.yml")
        result = board_import_closure(path)
        assert [p.relpath for p in result] == ["charts/a.yml"]


class TestLayoutImports:
    def test_simple_rows_import(self, project: FilesystemProject) -> None:
        write_board(project, "charts/_b.yml", "title: B\n")
        write_board(project, "charts/a.yml", "rows:\n  - _b.yml\n")
        path = project.path("charts/a.yml")
        result = board_import_closure(path)
        assert [p.relpath for p in result] == ["charts/a.yml", "charts/_b.yml"]

    def test_transitive_import(self, project: FilesystemProject) -> None:
        write_board(project, "charts/_c.yml", "title: C\n")
        write_board(project, "charts/_b.yml", "rows:\n  - _c.yml\n")
        write_board(project, "charts/a.yml", "rows:\n  - _b.yml\n")
        path = project.path("charts/a.yml")
        result = board_import_closure(path)
        assert [p.relpath for p in result] == [
            "charts/a.yml",
            "charts/_b.yml",
            "charts/_c.yml",
        ]

    def test_diamond_import_deduplicates(self, project: FilesystemProject) -> None:
        """A imports B and C; both import D. D appears only once."""
        write_board(project, "charts/_d.yml", "title: D\n")
        write_board(project, "charts/_b.yml", "rows:\n  - _d.yml\n")
        write_board(project, "charts/_c.yml", "rows:\n  - _d.yml\n")
        write_board(project, "charts/a.yml", "rows:\n  - _b.yml\n  - _c.yml\n")
        path = project.path("charts/a.yml")
        result = board_import_closure(path)
        relpaths = [p.relpath for p in result]
        assert relpaths.count("charts/_d.yml") == 1

    def test_cols_import(self, project: FilesystemProject) -> None:
        write_board(project, "charts/_b.yml", "title: B\n")
        write_board(project, "charts/a.yml", "cols:\n  - _b.yml\n")
        path = project.path("charts/a.yml")
        result = board_import_closure(path)
        assert "charts/_b.yml" in [p.relpath for p in result]

    def test_tabs_items_import(self, project: FilesystemProject) -> None:
        write_board(project, "charts/_tab.yml", "title: Tab\n")
        write_board(
            project,
            "charts/a.yml",
            "tabs:\n  items:\n    - _tab.yml\n",
        )
        path = project.path("charts/a.yml")
        result = board_import_closure(path)
        assert "charts/_tab.yml" in [p.relpath for p in result]


class TestCrossFileRefs:
    def test_query_ref(self, project: FilesystemProject) -> None:
        write_board(
            project, "charts/_shared.yml", "queries:\n  rev:\n    sql: SELECT 1\n"
        )
        write_board(
            project,
            "charts/a.yml",
            "queries:\n  rev:\n    ref: _shared.yml.queries.rev\n",
        )
        path = project.path("charts/a.yml")
        result = board_import_closure(path)
        assert "charts/_shared.yml" in [p.relpath for p in result]

    def test_chart_ref(self, project: FilesystemProject) -> None:
        write_board(
            project, "charts/_lib.yml", "charts:\n  kpi:\n    type: kpi\n    value: x\n"
        )
        write_board(
            project,
            "charts/a.yml",
            "charts:\n  kpi:\n    ref: _lib.yml.charts.kpi\n",
        )
        path = project.path("charts/a.yml")
        result = board_import_closure(path)
        assert "charts/_lib.yml" in [p.relpath for p in result]

    def test_bare_string_query_ref(self, project: FilesystemProject) -> None:
        """Bare-string cross-file query ref (canonical authored form per refs.py).

        queries:
          rev: _shared.yml.queries.rev   # coerced to VariableRef at parse time

        The closure walker reads authored YAML, not the Pydantic-normalized form,
        so it must detect bare strings, not just {ref: ...} dicts.
        """
        write_board(
            project, "charts/_shared.yml", "queries:\n  rev:\n    sql: SELECT 1\n"
        )
        write_board(
            project,
            "charts/a.yml",
            "queries:\n  rev: _shared.yml.queries.rev\n",
        )
        path = project.path("charts/a.yml")
        result = board_import_closure(path)
        assert "charts/_shared.yml" in [p.relpath for p in result]

    def test_bare_string_variable_ref(self, project: FilesystemProject) -> None:
        """Bare-string variable ref: variables:\n  regn: _v.yml.variables.regn"""
        write_board(
            project, "charts/_v.yml", "variables:\n  regn:\n    default: EMEA\n"
        )
        write_board(
            project,
            "charts/a.yml",
            "variables:\n  regn: _v.yml.variables.regn\n",
        )
        path = project.path("charts/a.yml")
        result = board_import_closure(path)
        assert "charts/_v.yml" in [p.relpath for p in result]

    def test_extends_file_ref(self, project: FilesystemProject) -> None:
        """extends: _base.yml imports the base board into the closure."""
        write_board(project, "charts/_base.yml", "title: Base\n")
        write_board(
            project,
            "charts/a.yml",
            "extends: _base.yml\n",
        )
        path = project.path("charts/a.yml")
        result = board_import_closure(path)
        assert "charts/_base.yml" in [p.relpath for p in result]

    def test_prose_mentioning_ref_sections_is_not_a_ref(
        self, project: FilesystemProject
    ) -> None:
        """Ordinary prose like 'style.charts.callout' must not be read as a
        cross-file ref — the ref grammar is anchored (refs.py), not a substring
        scan. A false positive here becomes a CompilationError and silently
        disables invalidation for the whole board."""
        write_board(
            project,
            "charts/a.yml",
            "title: A\n"
            "notes: >\n"
            "  Set style.charts.callout for tone, and read\n"
            "  theme.charts.single_series_palette[0] for the accent.\n",
        )
        result = board_import_closure(project.path("charts/a.yml"))
        assert [p.relpath for p in result] == ["charts/a.yml"]

    def test_extends_list_with_file_and_theme(self, project: FilesystemProject) -> None:
        """extends: [cream, _base.yml] — theme entries ignored, file refs included."""
        write_board(project, "charts/_base.yml", "title: Base\n")
        write_board(
            project,
            "charts/a.yml",
            "extends:\n  - cream\n  - _base.yml\n",
        )
        path = project.path("charts/a.yml")
        result = board_import_closure(path)
        assert "charts/_base.yml" in [p.relpath for p in result]


class TestMarkdownBoards:
    def test_markdown_board_does_not_raise(self, project: FilesystemProject) -> None:
        """A .md board with YAML frontmatter must not raise — it returns [root]."""
        write_board(
            project,
            "charts/a.md",
            "---\ntitle: My Board\n---\n\n# Hello\n",
        )
        path = project.path("charts/a.md")
        result = board_import_closure(path)
        assert [p.relpath for p in result] == ["charts/a.md"]

    def test_bad_yaml_becomes_compilation_error(
        self, project: FilesystemProject
    ) -> None:
        """A board with a YAML parse error raises CompilationError, not yaml.YAMLError."""
        write_board(project, "charts/a.yml", "title: {\ninvalid yaml\n")
        with pytest.raises(CompilationError):
            board_import_closure(project.path("charts/a.yml"))

    def test_malformed_markdown_frontmatter_becomes_compilation_error(
        self, project: FilesystemProject
    ) -> None:
        """Malformed .md boards raise CompilationError, never the parser's raw
        ValueError/yaml.YAMLError — board_view has no try and would 500."""
        cases = {
            "charts/unterminated.md": "---\ntitle: My Board\n\n# no closing fence\n",
            "charts/bad_frontmatter.md": "---\ntitle: {\ninvalid\n---\n\n# Hello\n",
            "charts/non_mapping.md": "---\n- just\n- a list\n---\n\n# Hello\n",
        }
        for relpath, content in cases.items():
            write_board(project, relpath, content)
            with pytest.raises(CompilationError):
                board_import_closure(project.path(relpath))

    def test_path_escape_becomes_compilation_error(
        self, project: FilesystemProject
    ) -> None:
        """An import path that escapes the project root raises CompilationError."""
        write_board(
            project,
            "charts/a.yml",
            "rows:\n  - ../../outside.yml\n",
        )
        path = project.path("charts/a.yml")
        with pytest.raises(CompilationError):
            board_import_closure(path)


class TestErrorCases:
    def test_cycle_raises(self, project: FilesystemProject) -> None:
        """A imports B, B imports A — cycle must be detected."""
        write_board(project, "charts/_b.yml", "rows:\n  - a.yml\n")
        write_board(project, "charts/a.yml", "rows:\n  - _b.yml\n")
        path = project.path("charts/a.yml")
        with pytest.raises(CompilationError, match="cycle"):
            board_import_closure(path)

    def test_missing_file_raises(self, project: FilesystemProject) -> None:
        write_board(project, "charts/a.yml", "rows:\n  - _missing.yml\n")
        path = project.path("charts/a.yml")
        with pytest.raises(CompilationError, match="_missing.yml"):
            board_import_closure(path)

    def test_self_import_raises(self, project: FilesystemProject) -> None:
        """A imports itself directly — cycle on first step."""
        write_board(project, "charts/a.yml", "rows:\n  - a.yml\n")
        path = project.path("charts/a.yml")
        with pytest.raises(CompilationError, match="cycle"):
            board_import_closure(path)


class TestProjectImportRefs:
    def test_walk_asks_the_project_for_refs(
        self, project: FilesystemProject, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_board(project, "charts/_b.yml", "title: B\n")
        write_board(project, "charts/a.yml", "rows:\n  - _b.yml\n")
        asked: list[str] = []
        real = type(project).import_refs

        def spy(self: FilesystemProject, relpath: str) -> ImportRefs:
            asked.append(relpath)
            return real(self, relpath)

        monkeypatch.setattr(type(project), "import_refs", spy)
        result = board_import_closure(project.path("charts/a.yml"))
        assert [p.relpath for p in result] == ["charts/a.yml", "charts/_b.yml"]
        assert asked == ["charts/a.yml", "charts/_b.yml"]

    def test_refs_override_replaces_the_file_read(
        self, project: FilesystemProject, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_board(project, "charts/_b.yml", "title: B\n")
        write_board(project, "charts/a.yml", "title: A\n")

        def stored(self: FilesystemProject, relpath: str) -> ImportRefs:
            if relpath == "charts/a.yml":
                return ImportRefs(imports=(("_b.yml", "file"),))
            return ImportRefs()

        monkeypatch.setattr(type(project), "import_refs", stored)
        result = board_import_closure(project.path("charts/a.yml"))
        assert [p.relpath for p in result] == ["charts/a.yml", "charts/_b.yml"]
