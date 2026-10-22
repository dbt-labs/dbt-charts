"""Tests that the theme is applied exactly once during board compilation.

Bug: the theme YAML was merged into authored_style via the extends/meta chain in
merge.py, then re-applied by the normalizer via get_theme_style(). The double
application was masked because all Merge markers are OVERRIDE (idempotent), but
authored_style contained theme data it shouldn't, and meta-level themes were not
correctly surfaced as the effective theme.

Fix: _resolve_entry returns EMPTY_PATCH for built-in theme names when theme_sink
is not None (the board-compile lane), and instead appends the theme name to
theme_sink. compiler.py passes theme_sink=[], reads the last entry as the
effective theme, and injects it into merged_board_data["extends"] so the
normalizer applies the theme exactly once.
"""

from __future__ import annotations

import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile_file
from dbt_charts.core.compile.config import (
    get_default_theme_name,
    get_theme_style,
    user_facing_theme_names,
)


def _non_default_themes() -> list[str]:
    """Return complete (loadable), author-facing built-in themes that are not
    the default.

    Uses ``user_facing_theme_names()``, not ``list_built_in_themes()`` — the
    latter includes ``_base`` and the diagnostics-only fixtures, which
    extend ``stark`` without overriding fields like
    ``background``, so two of them can collide on every field this test
    diffs on.
    """
    default = get_default_theme_name()
    result = []
    for t in user_facing_theme_names():
        if t == default:
            continue
        try:
            get_theme_style(t)
            result.append(t)
        except PydanticValidationError:
            pass  # skip partial/base themes that don't validate as a complete Style
    return result


def _pick_two_non_default() -> tuple[str, str]:
    themes = _non_default_themes()
    if len(themes) < 2:
        pytest.skip("Need at least 2 non-default themes")
    return themes[0], themes[1]


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    boards = tmp_path / "charts"
    boards.mkdir()
    return tmp_path


class TestMetaThemeBaseSelection:
    """Test 1: theme declared in meta.yml propagates as board.theme."""

    def test_meta_theme_is_effective_theme(
        self, project_dir: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        non_default = _non_default_themes()
        if not non_default:
            pytest.skip("No non-default themes available")

        chosen_theme = non_default[0]
        boards_dir = project_dir / "charts"

        (boards_dir / "meta.yml").write_text(
            textwrap.dedent(
                f"""\
                theme: {chosen_theme}
            """
            )
        )
        (boards_dir / "f.yaml").write_text(
            textwrap.dedent(
                """\
                rows:
                  - cols:
                    - text: hello
            """
            )
        )

        result = compile_file(
            local_project(project_dir).path("charts/f.yaml").read_board(),
            apply_meta=True,
        )
        assert not result.errors, result.errors
        assert result.board is not None

        # The effective theme must be the one declared in meta, not the default.
        assert result.board.theme == chosen_theme, (
            f"Expected theme={chosen_theme!r} from meta, got {result.board.theme!r}"
        )

        # The resolved_style must match what get_theme_style returns for the
        # chosen theme on at least one structurally-comparable field.
        expected_style = get_theme_style(chosen_theme)
        default_style = get_theme_style()

        # Find a field that differs between the two themes (structural comparison).
        board_bg = result.board.resolved_style.background
        chosen_bg = expected_style.background
        default_bg = default_style.background

        # Only assert the structural match when the themes actually differ
        # on this field; otherwise the test would pass vacuously.
        if chosen_bg != default_bg:
            assert board_bg == chosen_bg, (
                f"resolved_style.background={board_bg!r} should match "
                f"{chosen_theme!r} theme value {chosen_bg!r}"
            )


class TestThemeFreeAuthoredStyle:
    """Test 2: when extends is a built-in theme and there is no own style:
    block, authored_style must be None after compilation.
    """

    def test_authored_style_is_none_when_extends_is_theme(
        self, project_dir: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        non_default = _non_default_themes()
        if not non_default:
            pytest.skip("No non-default themes available")

        chosen_theme = non_default[0]
        boards_dir = project_dir / "charts"

        (boards_dir / "f.yaml").write_text(
            textwrap.dedent(
                f"""\
                extends: {chosen_theme}
                rows:
                  - cols:
                    - text: hello
            """
            )
        )

        result = compile_file(
            local_project(project_dir).path("charts/f.yaml").read_board(),
            apply_meta=True,
        )
        assert not result.errors, result.errors
        assert result.board is not None

        assert result.board.authored_style is None, (
            f"authored_style should be None when extends is a built-in theme "
            f"and no own style: block exists; got {result.board.authored_style!r}"
        )


class TestThemeSwitch:
    """Test 3: switching themes from theme_A to theme_B on an already-compiled
    board correctly updates resolved_style when authored_style is not polluted by
    old theme data.
    """

    def test_theme_switch_changes_resolved_style(
        self, project_dir: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        theme_a, theme_b = _pick_two_non_default()
        boards_dir = project_dir / "charts"

        style_a = get_theme_style(theme_a)
        style_b = get_theme_style(theme_b)

        # Find a field that actually differs between the two themes.
        if style_a.background == style_b.background:
            # If backgrounds match, try charts palette.
            if style_a.charts.palette == style_b.charts.palette:
                pytest.skip(
                    f"Themes {theme_a!r} and {theme_b!r} are identical on "
                    "tested fields; pick a different pair."
                )
            field = "palette"
        else:
            field = "background"

        (boards_dir / "f.yaml").write_text(
            textwrap.dedent(
                f"""\
                extends: {theme_a}
                rows:
                  - cols:
                    - text: hello
            """
            )
        )

        result = compile_file(
            local_project(project_dir).path("charts/f.yaml").read_board(),
            apply_meta=True,
        )
        assert not result.errors, result.errors
        board = result.board
        assert board is not None

        # Verify starting state
        assert board.theme == theme_a, (
            f"Expected theme_a={theme_a!r}, got {board.theme!r}"
        )

        # Switch theme
        board.set_theme(theme_b)
        assert board.theme == theme_b

        if field == "background":
            assert board.resolved_style.background == style_b.background, (
                f"After set_theme({theme_b!r}), background should be "
                f"{style_b.background!r}, got {board.resolved_style.background!r}"
            )
            # And it must differ from theme_a's value
            assert board.resolved_style.background != style_a.background
        else:
            assert board.chart_style_context.palette == style_b.charts.palette, (
                f"After set_theme({theme_b!r}), charts.palette should be "
                f"{style_b.charts.palette!r}, got {board.chart_style_context.palette!r}"
            )
            assert board.chart_style_context.palette != style_a.charts.palette
