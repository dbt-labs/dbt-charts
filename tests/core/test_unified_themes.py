"""Tests for the unified themes/ directory.

Covers: directory structure, YAML parse/validate, extends chain, get_theme_style,
and compiled Vega-Lite config output.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import dbt_charts as _dbt_charts_pkg
from dbt_charts.core.compile.config import user_facing_theme_names

DBT_CHARTS_PKG_DIR = Path(_dbt_charts_pkg.__file__).resolve().parent

_THEMES_DIR = DBT_CHARTS_PKG_DIR / "core" / "defaults" / "themes"


def test_structural_root_theme_parses_as_compiled_style():
    """themes/stark.yaml compiles to a valid Style through the inheritance chain.

    stark.yaml now extends _base — the raw YAML only contains color/font overrides.
    Compilation via get_theme_style() merges _base + stark into a full Style.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.theme import Style

    theme_path = _THEMES_DIR / "stark.yaml"
    assert theme_path.exists(), "themes/stark.yaml not found"

    cs = get_theme_style("stark")
    assert isinstance(cs, Style)


def test_get_theme_style_returns_compiled_style():
    """get_theme_style('clarity') returns a Style instance."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.theme import Style

    cs = get_theme_style("clarity")
    assert isinstance(cs, Style)


def test_get_theme_style_default_is_dbt_charts_default():
    """get_theme_style(None) returns a Style (the default theme)."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.theme import Style

    cs = get_theme_style(None)
    assert isinstance(cs, Style)


def test_unified_theme_extends_chain():
    """A theme that extends another gets its parent's values filled in."""
    from dbt_charts.core.compile.config import get_theme_style

    # neon theme extends stark; background is overridden
    neon = get_theme_style("neon")
    stark = get_theme_style("stark")
    assert neon.background != stark.background


def test_vega_config_helper_returns_dict():
    """The compiled Vega-Lite config is a dict."""
    from ._board_utils import _default_resolved_style

    result = _default_resolved_style().vega_config
    assert isinstance(result, dict)


def test_get_theme_style_raises_for_unknown_theme():
    """get_theme_style raises CompilationError for an unknown theme name."""
    from dbt_charts.core.compile.config import get_theme_style, reset_config
    from dbt_charts.core.compile.errors import CompilationError

    reset_config()
    with pytest.raises(CompilationError, match="nonexistent-theme-xyz"):
        get_theme_style("nonexistent-theme-xyz")


def test_board_style_fields_reachable():
    """A theme override on frame.margin propagates to the compiled style."""
    from dbt_charts.core.compile.config import get_theme_style, reset_config

    reset_config()
    base = get_theme_style("clarity")
    # Distinctive override — 999.0 cannot be confused with any real theme value.
    updated = base.model_copy(
        update={"frame": base.frame.model_copy(update={"margin": 999.0})}
    )
    assert updated.frame.margin == 999.0
    # Original unaffected
    assert base.frame.margin != 999.0


def test_charts_dimensions_reachable():
    """A theme override on charts.aspect_ratio propagates to the compiled style."""
    from dbt_charts.core.compile.config import get_theme_style, reset_config

    reset_config()
    base = get_theme_style("clarity")
    updated = base.model_copy(
        update={"charts": base.charts.model_copy(update={"aspect_ratio": 2.0})}
    )
    assert updated.charts.aspect_ratio == 2.0
    assert base.charts.aspect_ratio != 2.0


def test_default_background_self_token_propagates_to_knockout_strokes():
    """The shipped default theme's background feeds knockout strokes.

    Chart strokes that knockout against ``theme.background`` (pie/donut slice
    separator, geoshape/map region halos) must track the theme's own
    background via the self-token mechanism, whatever that background is.
    """
    from dbt_charts.core.compile.config import get_theme_style, reset_config

    reset_config()
    default = get_theme_style("clarity")
    # Self-token cascade: pie.marks.slice stroke is the donut-slice knockout
    # separator; marks.geoshape stroke is the region halo. Both track
    # theme.background at theme-compile time (ADR-015 marks namespace).
    assert default.charts.marks.slice.stroke.color == default.background
    assert default.charts.marks.geoshape.stroke.color == default.background


@pytest.mark.slow
def test_diagnostics_themes_compile_and_resolve():
    """Nightly-lane check for the diagnostics-* stress-knob themes.

    These themes exercise extreme values for individual style fields (title
    angle, font size, color, etc.) and are carved out of the fast-lane
    compiled_themes fixture to keep PR CI snappy. The slow nightly run is the
    safety net that catches any theme that fails to compile or resolve.
    """
    from dbt_charts.core.compile.config import get_theme_style, list_built_in_themes
    from dbt_charts.core.compile.models.style.theme import Style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    names = [n for n in list_built_in_themes() if n.startswith("diagnostics-")]
    assert names, "No diagnostics-* themes found — update the fixture carve-out logic"
    errors: list[str] = []
    for name in names:
        try:
            theme = get_theme_style(name)
            assert isinstance(theme, Style)
            resolve_style(theme)
        except Exception as exc:  # noqa: BLE001 — batch-collects all compile failures
            errors.append(f"{name}: {exc}")
    assert not errors, "Diagnostics themes failed:\n" + "\n".join(errors)


def test_bar_theme_default_stack_is_none_string():
    """_base.yaml sets bar.stack to 'none'; stark (and all themes) inherit it."""
    from dbt_charts.core.compile.config import get_theme_style

    # Use stark (the first complete standalone theme; _base alone is incomplete).
    theme = get_theme_style("stark")
    assert theme.charts.bar.stack == "none"


def test_bar_chart_resolved_chart_stack_defaults_to_none_string():
    """Bar chart built through the cascade gets stack='none', not Python None."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    # Construct a bare bar chart with no explicit stack — V2 resolve must produce
    # stack='none' from the theme default, not leave it as Python None.
    chart = BarChart(id="test_bar", type="bar", x="month", y="revenue", query_name="q")
    board_style = resolve_chart_style_context(get_theme_style())
    resolved = resolve(chart, [], chart_style_context=board_style)
    assert resolved.stack == "none", f"expected 'none', got {resolved.stack!r}"


def test_list_built_in_themes_enumerates_via_iterdir_not_glob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_glob_traversable: Callable[[Path], Any],
) -> None:
    """Regression: list_built_in_themes must enumerate via iterdir(), never
    glob()/.stem — neither is part of the importlib.resources.Traversable
    protocol, and this test's double implements only the protocol."""
    from dbt_charts.core.compile import config as config_mod

    for name in ("zeta", "alpha", "mid"):
        (tmp_path / f"{name}.yaml").write_text("style: {}\n")
    (tmp_path / "not-a-theme.txt").write_text("ignore me\n")

    monkeypatch.setattr(
        config_mod, "_built_in_unified_theme_dir", no_glob_traversable(tmp_path)
    )
    config_mod.list_built_in_themes.cache_clear()
    try:
        names = config_mod.list_built_in_themes()
    finally:
        config_mod.list_built_in_themes.cache_clear()

    assert names == ["alpha", "mid", "zeta"]


@pytest.mark.parametrize("theme_name", user_facing_theme_names())
def test_title_color_matches_prose_color_in_every_built_in_theme(
    theme_name: str,
) -> None:
    """Titles, headings, and prose share one ink in every built-in theme.

    style.title.font is an InheritSlot: no pin means it inherits font.color.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    resolved_style = resolve_style(get_theme_style(theme_name))

    assert resolved_style.title.font.color == resolved_style.font.color
