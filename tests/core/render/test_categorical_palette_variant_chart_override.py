"""End-to-end: a scalar `name.variant` categorical palette reference,
authored as a *chart-level* style override, renders instead of crashing.

`CategoricalColorStyle.palette`/`single_series_palette` accept a bare
string as whole-list shorthand (`palette: vivid-10.dark`), resolved to
`list[str]` by `CategoricalColorStyle._expand_palette_names`, a
`model_validator(mode="before")` -- but a *chart-local* override goes
through the dynamically generated `CategoricalColorStylePatch`
(`build_patch_model_ext`), which only carries over validators declared on
its own base class, never ones defined directly on the compiled model it
patches. Before `CategoricalPaletteValidationMixin` existed, the patch
had no such validator at all: the scalar string survived normalization
unresolved, `is_color_token` matched its `name.variant` shape (the same
shape a single-color role token has), and `_resolve_color_tokens` routed
it through `color_from_theme` -- a role lookup -- instead of `palette()`.
Board panel design_panel.py's own generated schema offers exactly this
shorthand (`schema_names.py`'s `PaletteName` includes every
`<categorical-name>.<variant>` combination), so the crash was reachable
through the design panel's own list widget, not just hand-authored YAML.

Proves the full seam -- compile() -> render() -> RenderResult.board_error
-- not a hand-built model construction, since the patch-vs-compiled
validator gap only shows up once a *chart-level* override is parsed
through the real cascade.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.migrations import SchemaMigrationWarning
from dbt_charts.core.diagnostics import WARN_PALETTE_UNSUPPORTED
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render
from dbt_charts.core.render.render_result import RenderResult

_QUERY = """
queries:
  q1:
    type: values
    rows:
      - {x: "A", y: 2}
      - {x: "B", y: 3}
"""


def _board(chart_type: str, style_yaml: str) -> str:
    return f"""
title: Probe
{_QUERY}
charts:
  c1:
    query: q1
    type: {chart_type}
    x: x
    y: y
    style:
{style_yaml}
rows:
  - c1
"""


def _render_yaml(board_yaml: str) -> RenderResult:
    result = compile(board_yaml)
    assert result.success and result.board is not None, result.errors
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    return render(result.board, executor, format="svg")


def _render(chart_type: str, style_yaml: str) -> RenderResult:
    return _render_yaml(_board(chart_type, style_yaml))


def _board_error(chart_type: str, style_yaml: str) -> object | None:
    rendered = _render(chart_type, style_yaml)
    if rendered.board_error is not None:
        return rendered.board_error
    return rendered.chart_errors[0] if rendered.chart_errors else None


@pytest.mark.parametrize("chart_type", ["line", "bar"])
def test_chart_level_palette_variant_scalar_renders(chart_type: str) -> None:
    """`style.color.categorical.palette: category-6-tonal-blue.dark`, authored
    directly on a chart (not the board), must resolve, not crash with
    ERR-INTERNAL naming 'category-6-tonal-blue' as an unbound theme role."""
    style_yaml = """      color:
        categorical:
          palette: category-6-tonal-blue.dark
"""
    error = _board_error(chart_type, style_yaml)
    assert error is None, error


@pytest.mark.parametrize("chart_type", ["line", "bar"])
def test_chart_level_single_series_palette_variant_scalar_renders(
    chart_type: str,
) -> None:
    """Same shape, `single_series_palette` instead of `palette`."""
    style_yaml = """      color:
        categorical:
          single_series_palette: vivid-10.deep
"""
    error = _board_error(chart_type, style_yaml)
    assert error is None, error


@pytest.mark.parametrize("alias", ["RdYlGn:5", "RdYlGn_r"])
def test_chart_level_shorthand_alias_still_warns(alias: str) -> None:
    """A chart-level `palette:` authored with the shorthand `:N`/`_r` suffix
    on a known anti-pattern name must still surface WARN-PALETTE-UNSUPPORTED.

    `_resolve_color_tokens`'s whole-list branch used to gate on
    `is_warn_alias(value)`, an exact membership check against `_WARN_ALIASES`
    -- `"RdYlGn:5"` and `"RdYlGn_r"` are not exact members, so the branch
    resolved them to `list[str]` before `chart_context.py`/`_palette.py`
    ever saw the raw string, and `requested_alias_palette` came back `None`:
    the warning silently disappeared for every shorthand-suffixed alias.
    `resolve_palette_alias()` strips the shorthand the same way
    `_parse_palette_reference` does before checking `_WARN_ALIASES`, so the
    literal string survives for the detector to re-derive provenance from.
    """
    style_yaml = f"""      color:
        categorical:
          palette: {alias}
"""
    rendered = _render("bar", style_yaml)
    assert rendered.board_error is None
    assert not rendered.chart_errors
    codes = {w.code for w in rendered.warnings}
    assert WARN_PALETTE_UNSUPPORTED.code in codes


def test_chart_level_palette_variant_scalar_still_renders_no_regression() -> None:
    """The prior round's fix (a literal-name + variant scalar, not an
    anti-pattern alias) must still resolve to a list, not stay a raw
    string -- confirming this round's `requested is not None` guard didn't
    widen and swallow the non-alias case too."""
    style_yaml = """      color:
        categorical:
          palette: category-6-tonal-blue.dark
"""
    rendered = _render("bar", style_yaml)
    assert rendered.board_error is None
    assert not rendered.chart_errors
    codes = {w.code for w in rendered.warnings}
    assert WARN_PALETTE_UNSUPPORTED.code not in codes


@pytest.mark.parametrize("field", ["palette", "single_series_palette"])
def test_chart_level_shorthand_steps_plus_variant_renders(field: str) -> None:
    """`vivid-10:4.dark` -- shorthand steps composed with a trailing variant
    -- authored as a *chart-level* override, must render instead of
    crashing ERR-INTERNAL.

    `resolve_palette_alias` used to parse the `:N` shorthand before
    splitting off the variant, so `_parse_palette_reference` saw the tail
    `"4.dark"` where it expected an integer and raised -- reachable from
    every board path (`tokens.py`'s whole-list branch calls it), even
    though `palette()` called directly always split in the correct order.
    Regression for that ordering bug, at chart-patch scope.
    """
    style_yaml = f"""      color:
        categorical:
          {field}: vivid-10:4.dark
"""
    error = _board_error("bar", style_yaml)
    assert error is None, error


_GRADIENT_BOARD = """
title: Probe
queries:
  q1:
    type: values
    rows:
      - {{x: "A", y: 2, s: "m"}}
      - {{x: "B", y: 3, s: "m"}}
charts:
  c1:
    query: q1
    type: {chart_type}
    x: x
    y: {y}
    color: y
    style:
      color:
        gradient:
          palette: {palette}
rows:
  - c1
"""


@pytest.mark.parametrize(
    ("chart_type", "y", "palette_name"),
    [("bar", "y", "vivid-10.dark"), ("heatmap", "s", "editorial-10.pale")],
)
def test_a_name_variant_in_a_gradient_slot_renders(
    chart_type: str, y: str, palette_name: str
) -> None:
    """`style.color.gradient.palette: vivid-10.dark` names a whole list for
    `bake_scale_target_stops` to resolve at channel time. `_resolve_color_tokens`
    used to route that scalar to the single-color resolver because
    `is_color_token("vivid-10.dark")` is True -- "theme has no palette assigned
    to role 'vivid-10'", ERR-INTERNAL at render. A `palette` string is a name
    in every slot that carries one, never a color token, so the walk must skip
    it by field name (as `_walk` already does) and leave the bake to the
    channel resolver.
    """
    rendered = _render_yaml(
        _GRADIENT_BOARD.format(chart_type=chart_type, y=y, palette=palette_name)
    )
    assert rendered.board_error is None, rendered.board_error
    assert not rendered.chart_errors, rendered.chart_errors
    assert rendered.output


def test_a_retired_gradient_spelling_migrates_in_memory_and_renders() -> None:
    """The Cloud path: a board still authoring `gradient.palette: vivid-10-dark`
    is respelled in memory by the identity Move `moves()` declares for every
    `palette`-tailed path -- gradient slots included -- to `vivid-10.dark`,
    and that respelled scalar must then render, not crash in the very walk
    the migration hands it to."""
    board = _GRADIENT_BOARD.format(chart_type="bar", y="y", palette="vivid-10-dark")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        rendered = _render_yaml(board)
    assert rendered.board_error is None, rendered.board_error
    assert not rendered.chart_errors, rendered.chart_errors
    assert rendered.output


def test_single_series_token_through_a_role_bound_to_a_name_variant_renders() -> None:
    """`style.palettes: {category: vivid-10.deep}` validates; a chart-level
    `single_series_palette: [category[1]]` under that binding must render.
    `_color_from_theme_dispatch` used to look the bound value up in the
    catalog verbatim -- "palette 'vivid-10.deep' (role 'category') not found
    in catalog", ERR-INTERNAL."""
    board = """
title: Probe
queries:
  q1:
    type: values
    rows:
      - {x: "A", y: 2}
      - {x: "B", y: 3}
style:
  palettes:
    category: vivid-10.deep
charts:
  c1:
    query: q1
    type: bar
    x: x
    y: y
    style:
      color:
        categorical:
          single_series_palette:
            - category[1]
rows:
  - c1
"""
    rendered = _render_yaml(board)
    assert rendered.board_error is None, rendered.board_error
    assert not rendered.chart_errors, rendered.chart_errors
    assert rendered.output
