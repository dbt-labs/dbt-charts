"""TDD tests for palette extends: inheritance (Task E).

Tests cover:
1. Child inherits parent aliases — resolve inherited alias on child palette
2. dbt-creams.ink resolves to cream hex (not gray) — key regression guard
3. Child aliases key overrides parent key
4. Transitive extends (c extends b extends a) merges correctly
5. Cycle (a extends b extends a) raises at load time
6. Unknown parent raises at load time
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from dbt_charts.core.compile.resolve.style import palette as pal_mod
from dbt_charts.core.compile.resolve.style.palette import (
    UnknownPaletteError,
    color_from_theme,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_caches() -> None:
    pal_mod._spine_cache.clear()
    pal_mod._index = None


@pytest.fixture(autouse=True)
def clear_palette_caches():
    """Ensure each test starts with a clean cache."""
    _reset_caches()
    yield
    _reset_caches()


# ---------------------------------------------------------------------------
# 1 & 2. dbt-creams inherits dbt-grays aliases; ink resolves to cream hex
# ---------------------------------------------------------------------------
#
# These tests verify that after the dbt-creams migration (Task E):
#   - Aliases defined only in dbt-grays are accessible via dbt-creams
#   - Integer alias refs resolve against the CHILD's colors array
#
# They will FAIL until:
#   (a) dbt-creams.yml adds `extends: dbt-grays` and removes duplicate aliases
#   (b) _load_spine_merged() is implemented


class TestCreamsInheritsGraysAliases:
    def test_child_inherits_parent_alias_canvas(self):
        """dbt-creams canvas must resolve via inherited alias → cream colors[0] = #FAF7F0."""
        result = color_from_theme("chrome.canvas", palettes={"chrome": "dbt-creams"})
        assert result.lower() == "#faf7f0"

    def test_child_inherits_parent_alias_border(self):
        """border → 4 → dbt-creams.colors[3] = #DDD3C0 (not gray #DFE1E5)."""
        result = color_from_theme("chrome.border", palettes={"chrome": "dbt-creams"})
        assert result.lower() == "#ddd3c0"
        assert result.lower() != "#dfe1e5"

    def test_ink_resolves_to_cream_not_gray(self):
        """CRITICAL: ink on dbt-creams → index 11 → cream colors[10] = #2A2725.

        If extends merges by pre-baking parent hex values (not alias refs), ink
        would return #222222 (gray ink). This test guards that regression.
        """
        result = color_from_theme("chrome.ink", palettes={"chrome": "dbt-creams"})
        assert result.lower() == "#2a2725"
        assert result.lower() != "#222222"

    def test_ink_on_grays_still_returns_gray(self):
        """dbt-grays.ink is still #222222 after migration."""
        result = color_from_theme("chrome.ink", palettes={"chrome": "dbt-grays"})
        assert result.lower() == "#222222"


# ---------------------------------------------------------------------------
# 3. Transitive extends: gamma extends beta extends alpha
#
# Uses real temporary YAML files to avoid patching internals.
# ---------------------------------------------------------------------------


@pytest.fixture
def transitive_palette_dir(tmp_path: Path) -> Path:
    """Write three synthetic palettes into a temp scaffold directory."""
    scaffold_dir = tmp_path / "palettes" / "scaffold"
    scaffold_dir.mkdir(parents=True)

    (scaffold_dir / "alpha.yml").write_text(
        textwrap.dedent(
            """\
        name: alpha
        colors:
          - "#aaaaaa"
          - "#bbbbbb"
          - "#cccccc"
        aliases:
          deep: 1
          mid: 2
          shallow: 3
        """
        ),
        encoding="utf-8",
    )
    (scaffold_dir / "beta.yml").write_text(
        textwrap.dedent(
            """\
        name: beta
        extends: alpha
        colors:
          - "#111111"
          - "#222222"
          - "#333333"
        aliases:
          shallow: 1
        """
        ),
        encoding="utf-8",
    )
    (scaffold_dir / "gamma.yml").write_text(
        textwrap.dedent(
            """\
        name: gamma
        extends: beta
        colors:
          - "#ffffff"
          - "#eeeeee"
          - "#dddddd"
        aliases: {}
        """
        ),
        encoding="utf-8",
    )
    return tmp_path


class TestTransitiveExtends:
    def test_deep_resolves_against_gamma_colors(
        self, transitive_palette_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """gamma inherits deep from alpha (via beta). deep=1 → gamma.colors[0] = #ffffff."""
        monkeypatch.setattr(
            pal_mod, "_PALETTES_DIR", transitive_palette_dir / "palettes"
        )
        _reset_caches()
        result = color_from_theme("role.deep", palettes={"role": "gamma"})
        assert result.lower() == "#ffffff"

    def test_mid_resolves_against_gamma_colors(
        self, transitive_palette_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """gamma inherits mid from alpha (via beta). mid=2 → gamma.colors[1] = #eeeeee."""
        monkeypatch.setattr(
            pal_mod, "_PALETTES_DIR", transitive_palette_dir / "palettes"
        )
        _reset_caches()
        result = color_from_theme("role.mid", palettes={"role": "gamma"})
        assert result.lower() == "#eeeeee"

    def test_beta_shallow_resolves_against_beta_colors(
        self, transitive_palette_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """beta.shallow = 1 → beta.colors[0] = #111111 (beta overrides alpha's 3)."""
        monkeypatch.setattr(
            pal_mod, "_PALETTES_DIR", transitive_palette_dir / "palettes"
        )
        _reset_caches()
        result = color_from_theme("role.shallow", palettes={"role": "beta"})
        assert result.lower() == "#111111"


# ---------------------------------------------------------------------------
# 4. Cycle detection: a extends b extends a
# ---------------------------------------------------------------------------


@pytest.fixture
def cyclic_palette_dir(tmp_path: Path) -> Path:
    scaffold_dir = tmp_path / "palettes" / "scaffold"
    scaffold_dir.mkdir(parents=True)

    (scaffold_dir / "cycle-a.yml").write_text(
        textwrap.dedent(
            """\
        name: cycle-a
        extends: cycle-b
        colors:
          - "#aaaaaa"
        aliases: {}
        """
        ),
        encoding="utf-8",
    )
    (scaffold_dir / "cycle-b.yml").write_text(
        textwrap.dedent(
            """\
        name: cycle-b
        extends: cycle-a
        colors:
          - "#bbbbbb"
        aliases: {}
        """
        ),
        encoding="utf-8",
    )
    return tmp_path


class TestExtendsCycleDetection:
    def test_cycle_raises_at_load_time(
        self, cyclic_palette_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """extends cycle (cycle-a → cycle-b → cycle-a) must raise a clear error."""
        monkeypatch.setattr(pal_mod, "_PALETTES_DIR", cyclic_palette_dir / "palettes")
        _reset_caches()
        with pytest.raises(ValueError, match="cycle"):
            color_from_theme("role.x", palettes={"role": "cycle-a"})


# ---------------------------------------------------------------------------
# 5. Unknown parent raises at load time
# ---------------------------------------------------------------------------


@pytest.fixture
def bad_extends_palette_dir(tmp_path: Path) -> Path:
    scaffold_dir = tmp_path / "palettes" / "scaffold"
    scaffold_dir.mkdir(parents=True)

    (scaffold_dir / "child-bad.yml").write_text(
        textwrap.dedent(
            """\
        name: child-bad
        extends: nonexistent-parent
        colors:
          - "#aaaaaa"
        aliases: {}
        """
        ),
        encoding="utf-8",
    )
    return tmp_path


class TestUnknownParentError:
    def test_unknown_parent_raises(
        self, bad_extends_palette_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """extends: nonexistent-parent must raise UnknownPaletteError with the parent name."""
        monkeypatch.setattr(
            pal_mod, "_PALETTES_DIR", bad_extends_palette_dir / "palettes"
        )
        _reset_caches()
        with pytest.raises(UnknownPaletteError, match="nonexistent-parent"):
            color_from_theme("role.x", palettes={"role": "child-bad"})
