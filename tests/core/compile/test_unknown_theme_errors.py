"""ERR-UNKNOWN-THEME: an unresolvable theme name must fail compile, not fall back.

`theme:` is sugar for `extends:`, and `extends:` legitimately carries board
paths that resolve at a later stage. Only the plain-name arm — the one that can
*only* mean a built-in theme — is checked here.
"""

from __future__ import annotations

import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import get_args

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import CompileResult, compile, compile_file
from dbt_charts.core.compile.models.schema_names import ThemeName

_BODY = textwrap.dedent(
    """\
    queries:
      q:
        columns: [a, b]
        values: [[1, 2]]
    charts:
      c:
        type: bar
        query: q
        x: a
        y: b
    rows:
      - c
    """
)


def _board(header: str) -> str:
    return f"{header}\n{_BODY}"


def _codes(result: CompileResult) -> list[str]:
    return [e.code for e in result.errors]


class TestUnknownThemeName:
    def test_theme_sugar_with_unknown_name_errors(self) -> None:
        result = compile(_board("title: t\ntheme: totally-fake-theme"))

        assert not result.success
        assert "ERR-UNKNOWN-THEME" in _codes(result)

    def test_extends_with_unknown_plain_name_errors(self) -> None:
        result = compile(_board("title: t\nextends: totally-fake-theme"))

        assert not result.success
        assert "ERR-UNKNOWN-THEME" in _codes(result)

    def test_private_base_fragment_is_not_a_theme(self) -> None:
        for header in ("theme: _base", "extends: _base", "extends: [clarity, _base]"):
            result = compile(_board(f"title: t\n{header}"))

            assert not result.success, header
            assert "ERR-UNKNOWN-THEME" in _codes(result), header

    def test_extends_list_with_unknown_plain_name_errors(self) -> None:
        result = compile(_board("title: t\nextends: [clarity, editorial-cream]"))

        assert not result.success
        assert "ERR-UNKNOWN-THEME" in _codes(result)

    def test_error_message_names_the_bad_theme_and_the_available_ones(self) -> None:
        result = compile(_board("title: t\ntheme: editorial-cream"))

        message = result.errors[0].message
        assert "editorial-cream" in message
        assert "clarity" in message

    def test_message_claims_nothing_about_board_resolution(self) -> None:
        """Nothing resolves a board name at the positions this check fires.

        The message must not offer project-root lookup or a path as the fix —
        neither happens here, and a nested/in-memory `extends: ./base.yaml` is
        still dropped silently.
        """
        message = compile(_board("title: t\ntheme: papr")).errors[0].message

        assert "project root" not in message
        assert ".yaml" not in message

    def test_message_lists_only_author_facing_themes(self) -> None:
        """`_base` and the diagnostics-only themes are accepted but never offered."""
        result = compile(_board("title: t\ntheme: totally-fake-theme"))

        message = result.errors[0].message
        assert "_base" not in message
        assert "diagnostics-" not in message

    def test_close_name_gets_a_did_you_mean_hint(self) -> None:
        result = compile(_board("title: t\ntheme: papr"))

        assert result.errors[0].hint == "Did you mean 'paper'?"

    def test_nested_board_with_unknown_theme_errors(self) -> None:
        yaml_content = textwrap.dedent(
            """\
            title: t
            charts:
              c:
                type: bar
                query: q
                x: a
                y: b
            queries:
              q:
                columns: [a, b]
                values: [[1, 2]]
            rows:
              - theme: totally-fake-theme
                rows:
                  - c
            """
        )

        result = compile(yaml_content)

        assert not result.success
        assert "ERR-UNKNOWN-THEME" in _codes(result)


class TestValidThemesStillCompile:
    @pytest.mark.parametrize("theme", sorted(get_args(ThemeName)))
    def test_every_built_in_theme_compiles(self, theme: str) -> None:
        result = compile(_board(f"title: t\ntheme: {theme}"))

        assert result.success, result.errors

    @pytest.mark.parametrize("theme", sorted(get_args(ThemeName)))
    def test_every_built_in_theme_is_the_resolved_theme(self, theme: str) -> None:
        result = compile(_board(f"title: t\ntheme: {theme}"))

        assert result.board is not None
        assert result.board.theme == theme


class TestInternalThemesStillResolve:
    def test_diagnostics_only_theme_is_accepted(self) -> None:
        """Internal themes are not author-facing, but boards that use them compile."""
        result = compile(_board("title: t\ntheme: diagnostics-title-left"))

        assert result.success, result.errors


class TestBoardImportsAreNotThemeNames:
    """`extends:` path refs resolve in the merge layer — never flagged here."""

    @pytest.mark.parametrize(
        "entry", ["./base.yaml", "../shared/base.yml", "composition/_base.yaml"]
    )
    def test_path_ref_extends_is_not_flagged(self, entry: str) -> None:
        result = compile(_board(f"title: t\nextends: {entry}"))

        assert result.success, result.errors
        assert "ERR-UNKNOWN-THEME" not in _codes(result)

    def test_named_board_extends_still_resolves_in_a_project(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        charts = tmp_path / "charts"
        charts.mkdir()
        (tmp_path / "dbt_charts.yml").write_text("name: t\n", encoding="utf-8")
        # Named-board extends resolves from the project root, not charts/.
        (tmp_path / "base.yaml").write_text(
            "title: inherited title\n", encoding="utf-8"
        )
        (charts / "board.yaml").write_text(_board("extends: base"), encoding="utf-8")

        project = local_project(tmp_path)
        result = compile_file(project.path("charts/board.yaml").read_board())

        assert result.success, result.errors
        assert result.board is not None
        assert result.board.title == "inherited title"

    def test_close_board_name_gets_a_did_you_mean_hint(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A near-miss on a *theme* still suggests one from the board lane."""
        charts = tmp_path / "charts"
        charts.mkdir()
        (tmp_path / "dbt_charts.yml").write_text("name: t\n", encoding="utf-8")
        (charts / "board.yaml").write_text(_board("title: t\ntheme: papr"))

        result = compile_file(
            local_project(tmp_path).path("charts/board.yaml").read_board()
        )

        assert "ERR-EXTENDS-UNRESOLVED" in _codes(result)
        assert result.errors[0].hint == "Did you mean 'paper'?"

    def test_unknown_name_in_a_project_reports_the_board_lookup(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        charts = tmp_path / "charts"
        charts.mkdir()
        (tmp_path / "dbt_charts.yml").write_text("name: t\n", encoding="utf-8")
        (charts / "board.yaml").write_text(
            _board("title: t\ntheme: totally-fake-theme"), encoding="utf-8"
        )

        project = local_project(tmp_path)
        result = compile_file(project.path("charts/board.yaml").read_board())

        assert not result.success
        # The project lane rejects the entry in the extends layer, before
        # validation runs. That site did try the board lookup, so it gets the
        # code that can name the directory it searched — never ERR-INTERNAL.
        assert "ERR-EXTENDS-UNRESOLVED" in _codes(result)
        message = result.errors[0].message
        assert "totally-fake-theme" in message
        # The board arm is offered only here, where it was actually tried.
        assert "project-root board name" in message
        assert "_base" not in message
        assert "diagnostics-" not in message
        assert result.errors[0].hint is None


class TestPositionsNothingResolves:
    """Where no board lookup happens, a plain name can only be a theme.

    Both shapes below already ignored their `extends:` silently — nothing
    folds a chain outside `compile_file`'s root board. Erroring is the change;
    the message must not pretend a board lookup was attempted.
    """

    def test_project_board_name_in_the_in_memory_lane_errors(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        charts = tmp_path / "charts"
        charts.mkdir()
        (tmp_path / "dbt_charts.yml").write_text("name: t\n", encoding="utf-8")
        (tmp_path / "base.yaml").write_text("title: inherited\n", encoding="utf-8")

        # Same bytes that inherit under compile_file; base_dir does not fold
        # the chain, so the name resolves to nothing and must not pass.
        result = compile(
            _board("extends: base"),
            base_dir=local_project(tmp_path).directory("charts"),
        )

        assert "ERR-UNKNOWN-THEME" in _codes(result)
        assert "project root" not in result.errors[0].message

    def test_nested_board_extends_a_project_board_and_errors(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        charts = tmp_path / "charts"
        charts.mkdir()
        (tmp_path / "dbt_charts.yml").write_text("name: t\n", encoding="utf-8")
        (tmp_path / "base.yaml").write_text("title: inherited\n", encoding="utf-8")
        (charts / "board.yaml").write_text(
            textwrap.dedent(
                """\
                title: t
                charts:
                  c:
                    type: bar
                    query: q
                    x: a
                    y: b
                queries:
                  q:
                    columns: [a, b]
                    values: [[1, 2]]
                rows:
                  - extends: base
                    rows:
                      - c
                """
            ),
            encoding="utf-8",
        )

        result = compile_file(
            local_project(tmp_path).path("charts/board.yaml").read_board()
        )

        # merged_patch folds only the root board's chain; the nested one is
        # never resolved, so validation is the only thing that can catch it.
        assert "ERR-UNKNOWN-THEME" in _codes(result)
        assert "project root" not in result.errors[0].message

    def test_file_included_partial_is_checked(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """An import is loaded during normalization, long after validation ran."""
        charts = tmp_path / "charts"
        charts.mkdir()
        (tmp_path / "dbt_charts.yml").write_text("name: t\n", encoding="utf-8")
        (charts / "_partial.yml").write_text(
            "title: part\ntheme: totally-fake-theme\nrows:\n  - c\n",
            encoding="utf-8",
        )
        (charts / "board.yaml").write_text(
            textwrap.dedent(
                """\
                title: t
                queries:
                  q:
                    columns: [a, b]
                    values: [[1, 2]]
                charts:
                  c:
                    type: bar
                    query: q
                    x: a
                    y: b
                rows:
                  - c
                  - _partial.yml
                """
            ),
            encoding="utf-8",
        )

        result = compile_file(
            local_project(tmp_path).path("charts/board.yaml").read_board()
        )

        assert "ERR-UNKNOWN-THEME" in _codes(result)
