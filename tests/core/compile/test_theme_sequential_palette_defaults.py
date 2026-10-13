"""Every built-in theme sets an explicit sequential palette for heatmap and
geoshape gradient color, instead of inheriting `_base.yaml`'s raw `blues`
Vega scheme.

Regression: measured contrast of `blues`'s light end against cream's canvas
was 1.08:1 — well under WCAG's 3:1 floor, so low-value heatmap cells were
effectively invisible on cream. `neon` overrode to raw `viridis` (a rainbow
scheme unrelated to its blue brand identity) with the low-end problem
inverted.

Structural, not literal: assert the resolved palette is a dbt charts named
`dbt-seq-*` palette (not a raw Vega scheme like `blues`/`viridis`), not the
exact palette name — a retune of which named palette a theme picks is a
design call, not a regression (`dbt-charts/AGENTS.md`: "Don't pin theme/default
values in tests"). Iterates `list_built_in_themes()` directly rather than a
hand-written theme list so a future `themes/foo.yaml` that forgets to
override the palette is actually caught.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
    list_built_in_themes,
    reset_config,
)
from dbt_charts.core.compile.models.schema_names import VEGA_SCHEME_NAMES

# Scaffolding themes carry no real design intent for this palette — excluded
# the same way the task's own design conversation scoped "every real theme".
_NON_DESIGN_THEMES = {"_base"}


def _real_theme_names() -> list[str]:
    return sorted(
        name
        for name in list_built_in_themes()
        if name not in _NON_DESIGN_THEMES and not name.startswith("diagnostics-")
    )


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _assert_themed_sequential_palette(palette: object) -> None:
    assert isinstance(palette, str), f"expected a named palette string, got {palette!r}"
    assert palette not in VEGA_SCHEME_NAMES, (
        f"{palette!r} is a raw Vega scheme, not a themed dbt-seq-* palette"
    )
    assert palette.startswith("dbt-seq-"), (
        f"expected a dbt-seq-* sequential palette, got {palette!r}"
    )


@pytest.mark.parametrize("theme_name", _real_theme_names())
def test_heatmap_gradient_palette_is_themed(theme_name: str) -> None:
    style = get_theme_style(theme_name)
    _assert_themed_sequential_palette(style.charts.heatmap.color.gradient.palette)


@pytest.mark.parametrize("theme_name", _real_theme_names())
def test_geoshape_gradient_palette_is_themed(theme_name: str) -> None:
    style = get_theme_style(theme_name)
    _assert_themed_sequential_palette(style.charts.geoshape.color.gradient.palette)
