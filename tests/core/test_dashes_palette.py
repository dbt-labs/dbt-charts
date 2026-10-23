"""Tests for the theme-level `dashes` palette → encoding-level `strokeDash` scale.

Note: Vega-Lite's documented `config.range.dashPattern` key is not honored by
vl_convert (silently falls back to defaults). The dash range therefore lives
on the per-chart `encoding.strokeDash.scale.range` instead of config.range.
The model fields (`ThemeChartsConfig.dashes`, `ChartsStyle.dashes`,
`ResolvedChartsStyle.dashes`) still carry the canonical theme value; the
render layer reads it and stamps it onto the encoding directly.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
)

# Fixture used to exercise list-of-lists round-trip through the cascade — not
# the production palette. The shipped 5+1 palette is documented in the task
# worksheet; tests intentionally avoid pinning its exact values per the repo's
# "don't pin theme defaults in tests" convention (see dbt-charts/AGENTS.md).
SAMPLE_DASHES = [
    [4, 4],
    [8, 8],
    [4, 2, 1, 3],
    [2, 2],
    [8, 2, 1, 3],
    [],
]


class TestDashesCascade:
    """Dashes flow from authored StylePatch through compiled ChartsStyle
    into ResolvedChartsStyle. The render layer reads `merged_charts.dashes` and
    stamps it onto the `strokeDash` encoding."""

    def test_dashes_propagate_through_resolve_style(self) -> None:
        patch = StylePatch.model_validate({"charts": {"dashes": SAMPLE_DASHES}})

        ctx = resolve_chart_style_context(get_theme_style(), patch)

        assert ctx.dashes == SAMPLE_DASHES

    def test_default_theme_has_no_dashes(self) -> None:
        """An empty palette is the resolved no-dash state."""

        ctx = resolve_chart_style_context(get_theme_style())

        assert ctx.dashes == []


SERIES_DATA = [
    {"month": "Jan", "revenue": 100, "region": "north"},
    {"month": "Jan", "revenue": 80, "region": "south"},
    {"month": "Feb", "revenue": 120, "region": "north"},
    {"month": "Feb", "revenue": 90, "region": "south"},
]
