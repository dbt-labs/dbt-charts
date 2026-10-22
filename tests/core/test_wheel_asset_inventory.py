"""Completeness contract: every expected non-Python wheel asset is bundled.

The inventory below is the contract. The test builds the wheel (via the
session-scoped fixture in conftest.py) and asserts each entry is present.
Adding a new bundled-asset category (or restoring one that silently
disappeared) means: update both the wheel target in `pyproject.toml` and
`EXPECTED_ASSETS` in the same PR.

Anchor-per-category, not exhaustive: pinning one file per category catches
whole-category drops (e.g. a `packages` list that lost an entry) without
forcing inventory edits for every legitimate new theme or skill. The
trade-off: a partial-vendor regression (`<pkg>/__init__.py` ships but
submodules drop) would not be caught by the anchor alone. See
`test_packaging_mdsvg.py` for a deeper-than-anchor pin on `mdsvg/`.
"""

from __future__ import annotations

import pytest

# Pin every wheel-consuming test to one xdist worker so `built_dbt_charts_wheel`
# (session-scoped) only runs `uv build` once across the whole run.
pytestmark = pytest.mark.xdist_group("dbt_charts_wheel")

# Each tuple: (wheel path, reason this asset must be in the wheel).
# The reason is rendered in the failure message, so keep it specific.
EXPECTED_ASSETS: list[tuple[str, str]] = [
    # Peer packages — top-level packages alongside `dbt-charts/`, added by
    # `dbt-charts/_build_backend.py`. Pinning each `__init__.py` catches a
    # regression where the backend loses one.
    ("d3_format/__init__.py", "peer d3-format library"),
    ("mdsvg/__init__.py", "peer markdown-svg renderer"),
    # Fonts and license files. Render paths load these by absolute path at
    # runtime; a missing font is a crash, not a fallback. License files are
    # redistributed under SIL OFL terms.
    ("dbt_charts/core/render/fonts/InterVariable.ttf", "default UI font"),
    (
        "dbt_charts/core/render/fonts/InterVariable-Italic.ttf",
        "default UI italic font",
    ),
    ("dbt_charts/core/render/fonts/InterVariable.woff2", "Inter web font for HTML"),
    (
        "dbt_charts/core/render/fonts/InterVariable-Italic.woff2",
        "Inter italic web font for HTML",
    ),
    (
        "dbt_charts/core/render/fonts/InterVariable-Medium.ttf",
        "Inter Variable weight-500 static board — vl-convert cannot bind wght=500 "
        "from the variable file, see fonts/README.md",
    ),
    (
        "dbt_charts/core/render/fonts/InterVariable-SemiBold.ttf",
        "Inter Variable weight-600 static board — vl-convert cannot bind wght=600 "
        "from the variable file, see fonts/README.md",
    ),
    (
        "dbt_charts/core/render/fonts/DBTSansTabular-Regular.ttf",
        "dbt sans tabular (figure-width digits)",
    ),
    (
        "dbt_charts/core/render/fonts/DBTSansTabular-Regular.woff2",
        "dbt sans tabular web font — referenced by _font_face.css",
    ),
    (
        "dbt_charts/core/render/fonts/DBTSansTabular-Medium.ttf",
        "dbt Sans Tabular weight-500 static board — vl-convert cannot bind wght=500 "
        "from the variable file, see fonts/README.md",
    ),
    (
        "dbt_charts/core/render/fonts/DBTSansTabular-SemiBold.ttf",
        "dbt Sans Tabular weight-600 static board — vl-convert cannot bind wght=600 "
        "from the variable file, see fonts/README.md",
    ),
    (
        "dbt_charts/core/render/fonts/DBTSerifOldstyleTabular-Regular.ttf",
        "dbt serif oldstyle tabular",
    ),
    (
        "dbt_charts/core/render/fonts/DBTSerifOldstyleTabular-Regular.woff2",
        "dbt serif oldstyle tabular web font — referenced by _font_face.css",
    ),
    (
        "dbt_charts/core/render/fonts/DBTSerifOldstyleProportional-Regular.ttf",
        "dbt serif oldstyle proportional",
    ),
    (
        "dbt_charts/core/render/fonts/DBTSerifOldstyleProportional-Regular.woff2",
        "dbt serif oldstyle proportional web font — referenced by _font_face.css",
    ),
    (
        "dbt_charts/core/render/fonts/SourceSerif4Variable.woff2",
        "Source Serif 4 variable web font — subsetted to the shared text recipe",
    ),
    (
        "dbt_charts/core/render/fonts/SourceSerif4Variable.ttf",
        "Source Serif 4 variable — measured for layout, and painted by vl-convert "
        "in static exports; Adobe 4.004 instanced at opsz=14, see fonts/README.md",
    ),
    ("dbt_charts/core/render/fonts/SourceSerif4-Italic.ttf", "Source Serif 4 italic"),
    (
        "dbt_charts/core/render/fonts/SourceSerif4-Italic.woff2",
        "Source Serif 4 italic web font — subsetted to the shared text recipe",
    ),
    (
        "dbt_charts/core/render/fonts/SourceSerif4-Medium.ttf",
        "Source Serif 4 weight-500 static board (family dbt Serif Medium) — "
        "vl-convert cannot bind wght=500 from the variable file, see fonts/README.md",
    ),
    (
        "dbt_charts/core/render/fonts/SourceSerif4-SemiBold.ttf",
        "Source Serif 4 weight-600 static board (family dbt Serif SemiBold) — "
        "vl-convert cannot bind wght=600 from the variable file, see fonts/README.md",
    ),
    (
        "dbt_charts/core/render/fonts/SOURCE_SERIF_4_LICENSE.txt",
        "Source Serif 4 SIL OFL license — required when redistributing the font",
    ),
    (
        "dbt_charts/core/render/fonts/SourceCodePro-Regular.ttf",
        "vendored monospace font for strict code measurement",
    ),
    (
        "dbt_charts/core/render/fonts/SourceCodePro-Regular.woff2",
        "Source Code Pro web font — subsetted to the shared text recipe",
    ),
    (
        "dbt_charts/core/render/fonts/SOURCE_CODE_PRO_LICENSE.txt",
        "Source Code Pro SIL OFL license — required when redistributing the font",
    ),
    # Emoji fonts — loaded by dbt-charts/core/fonts.py and via _font_face.css
    # at HTML render time. SIL OFL licenses redistributed alongside.
    (
        "dbt_charts/core/render/fonts/NotoEmoji-Regular.ttf",
        "monochrome emoji font for SVG/PDF rendering",
    ),
    (
        "dbt_charts/core/render/fonts/NotoEmoji-Regular.woff2",
        "Noto Emoji web font — referenced by _font_face.css",
    ),
    (
        "dbt_charts/core/render/fonts/_font_face.css",
        "@font-face declarations injected into HTML output",
    ),
    (
        "dbt_charts/core/render/fonts/NOTO_EMOJI_LICENSE.txt",
        "Noto Emoji SIL OFL license",
    ),
    # Canonical YAML syntax reference — inlined into authoring prompts and the
    # Playground assistant.
    (
        "dbt_charts/DBT_CHARTS_SYNTAX.md",
        "canonical YAML syntax reference; the single source of truth for the authoring prompts",
    ),
    # `dct init` templates — read at runtime when scaffolding a new project.
    (
        "dbt_charts/agent_api/_init_templates/dbt_charts.yml",
        "dct init project config template",
    ),
    ("dbt_charts/agent_api/_init_templates/README.md", "dct init project README"),
    (
        "dbt_charts/agent_api/_init_templates/charts/meta.yml",
        "dct init directory-defaults template",
    ),
    (
        "dbt_charts/agent_api/_init_templates/charts/partials/.gitkeep",
        "dct init partials placeholder",
    ),
    # Default config + default theme — every render path loads these. Bundled
    # because `dbt-charts/core/defaults/` is inside the package; this pin proves
    # the package's data files survive any future `exclude` rule edit.
    ("dbt_charts/core/defaults/default_config.yml", "default project config"),
    (
        "dbt_charts/core/defaults/themes/_base.yaml",
        "_base theme — structural root every theme extends",
    ),
    (
        "dbt_charts/core/defaults/themes/clarity.yaml",
        "clarity theme — required by every render",
    ),
    (
        "dbt_charts/core/defaults/themes/stark.yaml",
        "stark theme — user-facing pick and diagnostics parent",
    ),
    # Palette tree — one anchor per subdirectory catches whole-subdirectory drops.
    (
        "dbt_charts/core/defaults/palettes/categorical/vivid-10.yml",
        "default categorical palette",
    ),
    (
        "dbt_charts/core/defaults/palettes/sequential/blue.yml",
        "default sequential palette",
    ),
    (
        "dbt_charts/core/defaults/palettes/diverging/blue-red.yml",
        "default diverging palette",
    ),
    (
        "dbt_charts/core/defaults/palettes/data/xkcd_colors.json",
        "xkcd color lookup — referenced by palette resolution",
    ),
    (
        "dbt_charts/core/defaults/palettes/scaffold/dbt-grays.yml",
        "neutral scaffold palette",
    ),
    (
        "dbt_charts/core/defaults/palettes/tone/negative.yml",
        "negative tone palette",
    ),
    (
        "dbt_charts/core/defaults/palettes/tone/info.yml",
        "default informational callout tone palette",
    ),
    # AI surface — the agent loop and MCP load these at runtime.
    (
        "dbt_charts/ai/skills/chart/SKILL.md",
        "anchor AI skill — proves dbt-charts/ai/skills/ tree is shipped",
    ),
    (
        "dbt_charts/ai/examples/boards/kpi-overview.yml",
        "anchor board specimen — proves dbt_charts/ai/examples/ tree is shipped",
    ),
    # Surface-macro renderer — wheel skills are authored with `{{ s_X }}`
    # macros expanded at runtime against this alias table.
    (
        "dbt_charts/agent_api/surface_aliases.yaml",
        "macro alias table for surface-aware skill rendering",
    ),
]


@pytest.mark.timeout(120)
@pytest.mark.parametrize(
    ("wheel_path", "reason"),
    EXPECTED_ASSETS,
    ids=[path for path, _ in EXPECTED_ASSETS],
)
def test_wheel_bundles_expected_asset(
    dbt_charts_wheel_entries: set[str], wheel_path: str, reason: str
) -> None:
    assert wheel_path in dbt_charts_wheel_entries, (
        f"missing bundled asset: {wheel_path!r}\n"
        f"why this asset must be in the wheel: {reason}\n"
        "If the drop was intentional, remove the entry from EXPECTED_ASSETS; "
        "otherwise restore it in pyproject.toml's `[tool.maturin]` or `_build_backend.py`."
    )
