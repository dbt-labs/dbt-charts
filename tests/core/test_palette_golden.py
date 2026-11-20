"""Golden-file tests for M2 palette spine YAMLs.

Catches: malformed palette YAML structure, wrong stop count, missing required
metadata. Sequential and diverging palettes use the spine ``colors:`` array
directly — no LUT files exist.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dbt_charts.core.compile.models.palette import PALETTE_WARN_RECOMMENDATIONS

from .._paths import DBT_CHARTS_PKG_DIR

PALETTES_DIR = DBT_CHARTS_PKG_DIR / "core" / "defaults" / "palettes"


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_str: str) -> float:
    h = hex_str.lstrip("#")
    r, g, b = int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255
    rl, gl, bl = _srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b)
    return 0.2126 * rl + 0.7152 * gl + 0.0722 * bl


def _wcag_contrast(hex_a: str, hex_b: str) -> float:
    """WCAG 2.1 relative-luminance contrast ratio. Test-local copy."""
    l1, l2 = _relative_luminance(hex_a), _relative_luminance(hex_b)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


# Warned palettes (RdYlGn, parula) are third-party ramps drawn as written, so
# the house-spine profile pinned below does not apply to them.
def _house_only(paths: list[Path]) -> list[Path]:
    return [p for p in paths if p.stem not in PALETTE_WARN_RECOMMENDATIONS]


def _all_spine_paths() -> list[Path]:
    roots = [PALETTES_DIR / "sequential", PALETTES_DIR / "diverging"]
    spines: list[Path] = []
    for root in roots:
        for path in sorted(root.glob("*.yml")):
            spines.append(path)
    return _house_only(spines)


# ============================================================================
# Structural invariants
# ============================================================================


class TestSpineStructure:
    @pytest.mark.parametrize("path", _all_spine_paths(), ids=lambda p: p.stem)
    def test_spine_has_exactly_11_stops(self, path: Path):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        colors = data["colors"]
        assert isinstance(colors, list)
        assert len(colors) == 11, (
            f"{path.name}: expected 11 color stops, got {len(colors)}"
        )
        for i, hex_ in enumerate(colors):
            assert isinstance(hex_, str) and hex_.startswith("#") and len(hex_) == 7, (
                f"{path.name}: stop {i} malformed: {hex_!r}"
            )

    @pytest.mark.parametrize("path", _all_spine_paths(), ids=lambda p: p.stem)
    def test_spine_has_required_metadata(self, path: Path):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for key in ("name", "family", "description"):
            assert key in data, f"{path.name}: missing '{key}'"
        assert data["family"] in ("sequential", "diverging")

    @pytest.mark.parametrize("path", _all_spine_paths(), ids=lambda p: p.stem)
    def test_no_lut_sibling_exists(self, path: Path):
        """LUT files must not exist — they are retired (Task G)."""
        lut_path = path.with_name(path.stem + ".lut.yml")
        assert not lut_path.exists(), (
            f"Stale LUT file found: {lut_path}. LUT files are retired — delete them."
        )


# Dark-canvas forks (`*-dark`) carry the MIRROR profile (dark recede/midpoint,
# bright pop) and are validated against light body text in
# ``test_dark_palette_forks.py``. The light-canvas profile pinned below does not
# apply to them, so they are excluded here.
def _all_sequential_paths() -> list[Path]:
    return _house_only(
        sorted(
            p
            for p in (PALETTES_DIR / "sequential").glob("*.yml")
            if "-dark" not in p.stem
        )
    )


def _all_diverging_paths() -> list[Path]:
    return _house_only(
        sorted(
            p
            for p in (PALETTES_DIR / "diverging").glob("*.yml")
            if "-dark" not in p.stem
        )
    )


class TestSpineWcagProfile:
    """Documents the known WCAG contrast profile of seq/div spines.

    Sequential and diverging palettes use the full 11-stop spine for chart
    fills. Dark spine stops intentionally fail WCAG AA against #222222 body
    text — that is the design. Table consumers use ``surface="table"`` to get
    a WCAG-safe sub-palette carved from the dense 120-stop OKLCH-interpolated
    curve (see the module docstring in ``palette.py`` and ``_table_surface_seq``
    / ``_table_surface_div``).

    Sequential palettes run light (stop 0) → dark (stop 10).
    Diverging palettes run dark (stop 0) → light midpoint (stop 5) → dark (stop 10).

    These tests PIN the known profile so that future spine retunes that break the
    expected contrast shape surface immediately.
    """

    _BODY = "#222222"
    _WCAG_AA = 4.5

    @pytest.mark.parametrize("path", _all_sequential_paths(), ids=lambda p: p.stem)
    def test_sequential_light_half_passes_wcag_aa(self, path: Path):
        """Seq stops 0–4 (light half) must pass WCAG AA against #222222."""
        colors = yaml.safe_load(path.read_text(encoding="utf-8"))["colors"]
        for i, stop in enumerate(colors[:5]):
            ratio = _wcag_contrast(stop, self._BODY)
            assert ratio >= self._WCAG_AA, (
                f"{path.name} stop {i} ({stop}): contrast {ratio:.2f} < 4.5 "
                f"(light half must be WCAG AA safe for table use)"
            )

    @pytest.mark.parametrize("path", _all_sequential_paths(), ids=lambda p: p.stem)
    def test_sequential_dark_end_fails_wcag_aa_intentionally(self, path: Path):
        """Seq stop 10 (darkest) must fail WCAG AA — documents intentional regression.

        Dark stops sit at L*≈0.25, producing contrast ratios well below 4.5:1
        against #222222.  This is by design: spines target chart fills, not text
        backgrounds.  Table consumers: use text_color overrides near the dark end.
        """
        colors = yaml.safe_load(path.read_text(encoding="utf-8"))["colors"]
        darkest = colors[-1]
        ratio = _wcag_contrast(darkest, self._BODY)
        assert ratio < self._WCAG_AA, (
            f"{path.name} darkest stop ({darkest}): contrast {ratio:.2f} >= 4.5 "
            f"(unexpected — spine retune changed the dark endpoint; "
            f"update this test if intentional)"
        )

    @pytest.mark.parametrize("path", _all_diverging_paths(), ids=lambda p: p.stem)
    def test_diverging_midpoint_passes_wcag_aa(self, path: Path):
        """Div stop 5 (light midpoint) must pass WCAG AA against #222222."""
        colors = yaml.safe_load(path.read_text(encoding="utf-8"))["colors"]
        midpoint = colors[5]
        ratio = _wcag_contrast(midpoint, self._BODY)
        assert ratio >= self._WCAG_AA, (
            f"{path.name} midpoint stop 5 ({midpoint}): contrast {ratio:.2f} < 4.5 "
            f"(midpoint must be WCAG AA safe)"
        )

    @pytest.mark.parametrize("path", _all_diverging_paths(), ids=lambda p: p.stem)
    def test_diverging_extremes_fail_wcag_aa_intentionally(self, path: Path):
        """Div stops 0 and 10 (dark extremes) must fail WCAG AA — intentional.

        Diverging palettes have dark ends at both extremes; contrast against
        #222222 is intentionally below 4.5:1 at the arms' dark ends.
        """
        colors = yaml.safe_load(path.read_text(encoding="utf-8"))["colors"]
        for i in (0, 10):
            stop = colors[i]
            ratio = _wcag_contrast(stop, self._BODY)
            assert ratio < self._WCAG_AA, (
                f"{path.name} extreme stop {i} ({stop}): contrast {ratio:.2f} >= 4.5 "
                f"(unexpected — spine retune changed a dark extreme; "
                f"update this test if intentional)"
            )
