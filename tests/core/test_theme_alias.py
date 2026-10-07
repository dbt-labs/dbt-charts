"""Tests for theme: → extends: parse-time alias (Lane C, Phase 3).

`theme: X` is authoring sugar for `extends: X`. A BeforeValidator on the
model's own Annotated input wrapper (AuthoredBoardInput / BoardPatchInput)
rewrites the key at parse time so the resolution engine sees only `extends:`.

Built-in theme YAMLs are Board fragments — they carry `extends:` and `style:`
and can be loaded as BoardPatch instances for use with merge_patches.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.board.authored import AUTHORED_BOARD_ADAPTER
from dbt_charts.core.compile.models.board.patch import BOARD_PATCH_ADAPTER


class TestThemeAliasDesugars:
    """theme: X desugars to extends: X at parse time."""

    def test_theme_becomes_extends(self) -> None:
        board = AUTHORED_BOARD_ADAPTER.validate_python(
            {"title": "test", "rows": [], "theme": "dark"}
        )
        assert board.extends == "dark"

    def test_theme_none_raises(self) -> None:
        with pytest.raises(ValidationError, match="must be a theme name string"):
            AUTHORED_BOARD_ADAPTER.validate_python(
                {"title": "test", "rows": [], "theme": None}
            )

    def test_extends_still_works_without_theme(self) -> None:
        board = AUTHORED_BOARD_ADAPTER.validate_python(
            {"title": "test", "rows": [], "extends": "paper"}
        )
        assert board.extends == "paper"

    def test_theme_and_extends_conflict_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AUTHORED_BOARD_ADAPTER.validate_python(
                {"title": "test", "rows": [], "theme": "neon", "extends": "stark"}
            )


class TestBuiltInThemesAsBoardFragments:
    """Built-in theme YAMLs can be loaded as BoardPatch (Board fragments)."""

    def test_theme_yaml_loads_as_board_patch(self) -> None:
        """A built-in theme YAML parses as a BoardPatch without errors."""
        import yaml

        import dbt_charts

        pkg_dir = __import__("pathlib").Path(dbt_charts.__file__).resolve().parent
        themes_dir = pkg_dir / "core" / "defaults" / "themes"
        neon_path = themes_dir / "neon.yaml"
        assert neon_path.exists()

        raw = yaml.safe_load(neon_path.read_text(encoding="utf-8"))
        patch = BOARD_PATCH_ADAPTER.validate_python(raw)
        # neon extends stark
        assert patch.extends == "stark"

    def test_all_built_in_themes_parse_as_board_patch(self) -> None:
        """Every built-in theme YAML validates as a BoardPatch."""
        import yaml

        import dbt_charts

        pkg_dir = __import__("pathlib").Path(dbt_charts.__file__).resolve().parent
        themes_dir = pkg_dir / "core" / "defaults" / "themes"
        theme_files = list(themes_dir.glob("*.yaml"))
        assert theme_files, "No theme YAMLs found"

        for path in theme_files:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            # Must not raise
            BOARD_PATCH_ADAPTER.validate_python(raw)
