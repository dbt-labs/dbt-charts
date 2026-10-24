"""``fonts.secondary`` is the secondary-TEXT ink — it must be legible per theme.

Regression for the muted-grade overload: stark once set the secondary ink to a
fill-grade near-white (right for spark/table bar tracks, which used to inherit
it), which made KPI support rows and table/spark secondary text invisible on
every light theme extending stark. The track backgrounds are pinned
explicitly in the themes; the secondary font must always read as text.

Thresholds are deliberately loose (no theme-hex pinning): text needs ~3:1
against the canvas; a bar track is background chrome and must stay subtle.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.colors import wcag_contrast as _wcag_contrast
from dbt_charts.core.compile.config import get_theme_style, list_built_in_themes

THEMES = [t for t in list_built_in_themes() if not t.startswith(("_", "diagnostics-"))]


@pytest.mark.parametrize("theme", THEMES)
def test_secondary_text_is_legible_on_theme_background(theme: str) -> None:
    style = get_theme_style(theme)
    assert style.fonts is not None
    ink = style.fonts["secondary"].color
    assert ink is not None
    contrast = _wcag_contrast(ink, style.background)
    assert contrast >= 3.0, (
        f"theme {theme!r}: secondary text {ink} on background "
        f"{style.background} is {contrast:.2f}:1 — secondary text "
        "(KPI support rows, table subtitles) would be illegible"
    )


@pytest.mark.parametrize("theme", THEMES)
def test_bar_tracks_stay_subtle_background_chrome(theme: str) -> None:
    style = get_theme_style(theme)
    for label, track in (
        ("charts.spark_bar.bar.background", style.charts.spark_bar.bar.background),
        ("charts.table.spark.bar.background", style.charts.table.spark.bar.background),
    ):
        contrast = _wcag_contrast(track, style.background)
        assert contrast < 2.0, (
            f"theme {theme!r}: {label} {track} on background "
            f"{style.background} is {contrast:.2f}:1 — a bar track is "
            "background chrome and must stay subtle, not read as a dark slab"
        )
