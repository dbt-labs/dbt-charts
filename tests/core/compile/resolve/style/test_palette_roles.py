"""A theme palette role may stand in for a palette name — in theme YAML only.

`palettes:` maps a role (`category`, `sequence`) to a palette file. Before this,
that map only served single-color tokens like `category[2]`, while the palette
fields repeated the file name — so a theme named the same palette twice with
nothing keeping the two in sync.

Roles are substituted by `expand_palette_refs`, which runs once, at
`get_theme_style()`, where the theme's own `palettes:` map is final. Board- and
chart-level `style:` cannot bind or use a role — the map does not exist at that
scope. A role-shaped name authored there, or a genuine typo, is caught by
`validate/palettes.py`'s post-normalize guard: both fail compile with
ERR-PALETTE-UNKNOWN, exactly as an unresolvable name always did before roles
existed.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.compile.config import (
    get_theme_style,
    list_built_in_themes,
    reset_config,
)
from dbt_charts.core.compile.resolve.style.palette import palette as stops_for


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _real_themes() -> list[str]:
    return sorted(
        n
        for n in list_built_in_themes()
        if n != "_base" and not n.startswith("diagnostics-")
    )


@pytest.mark.parametrize("theme_name", _real_themes())
def test_categorical_palette_resolves_through_the_category_role(theme_name):
    """Every theme authors `palette: category`; it must land on its own family."""
    style = get_theme_style(theme_name)
    bound = (style.palettes or {})["category"]
    assert style.charts.color.categorical.palette == stops_for(bound)


@pytest.mark.parametrize("theme_name", _real_themes())
def test_sequence_role_matches_the_gradients_it_feeds(theme_name):
    """The role and the gradients must name one palette, not two.

    `sequence` used to be bound once in `_base` and never rebound, so it claimed
    blue while stark and solid rendered gray, cream brown, and neon blue-dark —
    a `sequence[N]` token clashed with the gradient beside it.
    """
    style = get_theme_style(theme_name)
    bound = (style.palettes or {}).get("sequence")
    assert bound is not None
    for family in ("heatmap", "geoshape"):
        # A gradient keeps a *name* (it is carved downstream against the
        # consumer's surface), so the role has been substituted for the
        # palette it binds rather than expanded to stops.
        gradient = getattr(style.charts, family).color.gradient
        assert gradient.palette == bound, family


def test_role_ref_keeps_the_steps_shorthand():
    """`category:3` must sample the bound palette, not look up a literal name."""
    from dbt_charts.core.compile.resolve.style.palette import resolve_palette_ref

    bound = (get_theme_style().palettes or {})["category"]
    assert resolve_palette_ref("category:3", {"category": bound}) == f"{bound}:3"


def test_a_role_bound_to_a_missing_palette_is_rejected():
    """`style.palettes` values are schema-typed to shipped palette names — this
    is a plain field-validation error, unrelated to role substitution, and
    unaffected by scoping roles to theme YAML."""
    board = """
title: T
style:
  palettes:
    category: no-such-palette
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - revenue
"""
    result = compile_board(board)
    assert not result.success
    assert "no-such-palette" in result.errors[0].message


# ---------------------------------------------------------------------------
# Board-level `style:` — a role is unsupported; a typo still fails compile.
# ---------------------------------------------------------------------------


def _board(palette_value: str) -> str:
    return f"""
title: T
style:
  charts:
    color:
      categorical:
        palette: {palette_value}
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - revenue
"""


def test_board_may_not_name_a_role():
    """Board-level `style:` cannot bind `category` — roles are theme-scoped."""
    result = compile_board(_board("category"))
    assert not result.success
    assert result.errors[0].code == "ERR-PALETTE-UNKNOWN"


def test_currentcolor_inside_a_palette_list_compiles_and_inks_as_itself():
    """`currentColor` is bare-identifier-shaped -- matching the same shape
    as a candidate role name or a typo -- but it's a reserved CSS keyword,
    not a name that could ever resolve to a role, so it does not take the
    role deferral. It also is not a color dbt Charts can read -- no
    breaking change means this compiles, with the mark inked as itself."""
    result = compile_board(_board('["currentColor", "#4e79a7"]'))
    assert result.success, result.errors
    assert result.board is not None
    ctx = result.board.chart_style_context
    assert ctx.dark_companion_palette[0] == "currentColor"


@pytest.mark.parametrize("member", ["bloo", "category", "vivid-10"])
def test_a_bare_word_role_or_shipped_name_inside_a_palette_list_compiles(member):
    """No-breaking-change canary for the deleted list-member gate: a typo
    (`bloo`), a real role every theme binds (`category`), and a real
    shipped palette (`vivid-10`) all used to be rejected as one member of
    a literal color list. `mark_ink()` already inks an unreadable member
    as itself -- this compiles clean, first member unchanged, same as
    any other member it can't read as a color."""
    result = compile_board(_board(f'["{member}", "#4e79a7"]'))
    assert result.success, result.errors
    assert result.board is not None
    ctx = result.board.chart_style_context
    assert ctx.dark_companion_palette[0] == member


def test_a_css_keyword_member_beside_hex_stops_compiles():
    """`palette: ["navy", "#4e79a7"]` -- a CSS keyword name is real color
    data, not a candidate role name; it must not hit ERR-PALETTE-UNKNOWN
    just because a bare identifier also matches its shape."""
    result = compile_board(_board('["navy", "#4e79a7"]'))
    assert result.success, result.errors


def test_an_rgb_function_member_beside_hex_stops_compiles():
    """`palette: ["rgb(10,20,30)", "#4e79a7"]` -- the full parse_css_color
    grammar, not just is_sanitizable_color's hex-only subset, applies to
    each list member."""
    result = compile_board(_board('["rgb(10,20,30)", "#4e79a7"]'))
    assert result.success, result.errors


def test_an_eight_digit_hex_member_beside_hex_stops_compiles():
    """`palette: ["#4e79a7ff", "#4e79a7"]` -- an alpha-carrying hex stop is
    legal color data, same as a plain 6-digit hex."""
    result = compile_board(_board('["#4e79a7ff", "#4e79a7"]'))
    assert result.success, result.errors


def test_a_float_list_gradient_palette_is_not_treated_as_color_data():
    """`gradient.palette: [0.0, 0.5, 1.0]` -- ScaleTargetConfig.palette is a
    documented list[float] of relative stops on the gradient field, a
    different field that happens to share the name "palette" with the
    categorical list. A non-str member is skipped outright rather than
    run through is_sanitizable_color, which used to raise a raw TypeError."""
    result = compile_board(_gradient("[0.0, 0.5, 1.0]"))
    assert result.success, result.errors


def test_a_malformed_hex_member_beside_hex_stops_compiles_and_inks_as_itself():
    """`palette: ["#12345", "#4e79a7"]` -- a five-digit hex is neither a
    valid color nor a candidate role name. No breaking change: this
    compiles, and the unreadable stop's own ink is the string unchanged --
    render paints it as authored; there is no ink to derive."""
    result = compile_board(_board('["#12345", "#4e79a7"]'))
    assert result.success, result.errors
    assert result.board is not None
    ctx = result.board.chart_style_context
    assert ctx.dark_companion_palette[0] == "#12345"


def test_a_malformed_hex_member_in_a_chart_local_palette_compiles():
    """Chart-local color resolution never eagerly calls `mark_ink()` per
    palette member the way board-level style resolution does, so a
    malformed chart-local list member takes the same path as a board-level
    one: no breaking change means a color-shaped (not role-shaped) string
    is never rejected, list member or not."""
    result = compile_board(_chart_local("palette", '["#12345", "#4e79a7"]'))
    assert result.success, result.errors


def test_nested_board_style_role_is_rejected():
    """A nested board carries its own board-level style cascade, reachable only
    by recursing into `item.board` — unlike its charts, which normalization
    also hoists into every ancestor's `board.charts`. Without the recursion,
    this compiles `success=True` and the role never reaches the guard."""
    result = compile_board(
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
rows:
  - height: 600
    charts:
      inner:
        query: q
        type: bar
        x: month
        y: revenue
    style:
      charts:
        color:
          categorical:
            palette: category
    rows:
      - inner
"""
    )
    assert not result.success
    assert result.errors[0].code == "ERR-PALETTE-UNKNOWN"


def test_board_may_still_name_a_palette_file():
    """A resolvable literal name still expands to stops — the model
    validator is now the only expander for a board-level `palette:` field,
    since `_merge_style` no longer re-runs `expand_palette_refs`."""
    result = compile_board(_board("vivid-10"))
    assert result.success, [e.message for e in result.errors]
    assert result.board is not None
    assert list(result.board.chart_style_context.palette) == stops_for("vivid-10")


def test_unknown_name_is_rejected_with_a_did_you_mean():
    result = compile_board(_board("vivid-11"))

    assert not result.success
    assert result.errors[0].code == "ERR-PALETTE-UNKNOWN"
    assert result.errors[0].hint is not None
    assert "vivid-10" in result.errors[0].hint


def test_a_tone_in_a_stops_slot_is_a_diagnostic_not_a_traceback():
    """A tone resolves but names one color; `palette()` raises a subclass of
    ValueError that is not UnknownPaletteError, which must not escape."""
    result = compile_board(_board("negative"))
    assert not result.success
    assert "negative" in result.errors[0].message


def test_a_vega_scheme_is_rejected_in_a_stops_slot():
    """A scheme has no stops; unexpanded it reaches the SVG as fill="viridis"."""
    assert not compile_board(_board("viridis")).success


def test_a_perceptual_anti_pattern_is_still_hard_rejected():
    """`rainbow` is a deliberate hard-fail; deferring an unknown name must not
    quietly absorb it."""
    assert not compile_board(_board("rainbow")).success


def _gradient(value: str) -> str:
    return f"""
title: T
style:
  charts:
    heatmap:
      color:
        gradient:
          palette: {value}
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 'A' AS seg, 1 AS v
charts:
  h:
    query: q
    type: heatmap
    x: month
    y: seg
    color: v
rows:
  - h
"""


def test_gradient_keeps_its_name_but_the_name_is_checked():
    """A gradient is carved downstream, so the name survives — but a typo in it
    must not sail past compile and land as ERR-INTERNAL at render."""
    assert compile_board(_gradient("dbt-seq-blue")).success
    assert not compile_board(_gradient("dbt-seq-bleu")).success


def test_gradient_role_is_rejected_at_board_level():
    """A gradient role is unsupported outside theme YAML too."""
    result = compile_board(_gradient("sequence"))
    assert not result.success
    assert result.errors[0].code == "ERR-PALETTE-UNKNOWN"


# ---------------------------------------------------------------------------
# Chart-local `style:` (declared `charts:` blocks) — same rule: role rejected,
# typo rejected.
# ---------------------------------------------------------------------------


def _chart_local(field: str, value: str) -> str:
    return f"""
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 'A' AS seg, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    color: seg
    style:
      color:
        categorical:
          {field}: {value}
rows:
  - revenue
"""


def test_chart_local_role_is_rejected():
    result = compile_board(_chart_local("palette", "category"))
    assert not result.success
    assert result.errors[0].code == "ERR-PALETTE-UNKNOWN"


def test_chart_local_single_series_role_is_rejected():
    result = compile_board(_chart_local("single_series_palette", "category_ink"))
    assert not result.success
    assert result.errors[0].code == "ERR-PALETTE-UNKNOWN"


def test_chart_local_unknown_name_is_rejected():
    result = compile_board(_chart_local("palette", "vivid-11"))
    assert not result.success


def test_chart_local_table_column_scale_role_is_rejected():
    """`style.columns` is a dict keyed by column name, but each value is a
    real `TableColumnConfig` model — this reaches the guard through the
    ordinary model-field walk, not the dict-key-aware branch (that branch's
    only genuine target is KPI `background`'s raw `{column, scale}` spec,
    covered separately below)."""
    board = """
title: T
queries:
  q:
    source: db
    sql: SELECT 100 AS revenue
charts:
  t:
    query: q
    type: table
    style:
      columns:
        revenue:
          scale:
            background:
              palette: sequence
rows:
  - t
"""
    result = compile_board(board)
    assert not result.success
    assert result.errors[0].code == "ERR-PALETTE-UNKNOWN"


# ---------------------------------------------------------------------------
# KPI `background` — a raw `Any`-typed `{column, scale}` channel spec, never
# a pydantic model. This is the dict-key-aware branch's real target: without
# it, a role or typo here reaches `bake_scale_target_stops` at render and
# raises `UnknownPaletteError` there, which is ERR-INTERNAL for the board.
# ---------------------------------------------------------------------------


def _kpi_background(value: str) -> str:
    return f"""
title: T
queries:
  q:
    source: db
    sql: SELECT 100 AS revenue
charts:
  k:
    query: q
    type: kpi
    value: revenue
    background:
      column: revenue
      scale:
        palette: {value}
rows:
  - k
"""


def test_kpi_background_role_is_rejected():
    result = compile_board(_kpi_background("sequence"))
    assert not result.success
    assert result.errors[0].code == "ERR-PALETTE-UNKNOWN"


def test_kpi_background_typo_is_rejected():
    result = compile_board(_kpi_background("dbt-seq-bleu"))
    assert not result.success
    assert result.errors[0].code == "ERR-PALETTE-UNKNOWN"


def test_kpi_background_literal_palette_compiles():
    result = compile_board(_kpi_background("dbt-seq-blue"))
    assert result.success, [e.message for e in result.errors]


def test_kpi_background_vega_scheme_compiles():
    result = compile_board(_kpi_background("viridis"))
    assert result.success, [e.message for e in result.errors]


# ---------------------------------------------------------------------------
# A data column named `palette`/`single_series_palette` is not a style leaf.
# The guard walks `chart.style` and `chart.background` only — never
# `chart.query`, which carries author data (`ValuesQuery.rows` is
# `list[dict[str, Any]]`) — so a column happening to share a style field's
# name must compile exactly as if it were named anything else.
# ---------------------------------------------------------------------------


def test_values_query_column_named_palette_is_not_a_style_leaf():
    board = """
title: T
queries:
  q:
    columns: [month, palette]
    values: [["Jan", "brand-blue"]]
charts:
  b:
    query: q
    type: bar
    x: month
    y: palette
rows:
  - b
"""
    result = compile_board(board)
    assert result.success, [e.message for e in result.errors]


def test_values_query_column_named_single_series_palette_is_not_a_style_leaf():
    board = """
title: T
queries:
  q:
    columns: [month, single_series_palette]
    values: [["Jan", "brand-blue"]]
charts:
  b:
    query: q
    type: bar
    x: month
    y: single_series_palette
rows:
  - b
"""
    result = compile_board(board)
    assert result.success, [e.message for e in result.errors]


# ---------------------------------------------------------------------------
# Inline `rows:` charts — the shape that shipped both defects. Substitution
# never ran here (bug 1); the typo check never ran here either (bug 2). Both
# forms — bare inline chart, and named inline chart dict — must now fail
# compile instead of reaching render with a bare role or an unchecked typo.
# ---------------------------------------------------------------------------


def _inline_bare(field: str, value: str) -> str:
    return f"""
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 'A' AS seg, 100 AS revenue
rows:
  - type: bar
    query: q
    x: month
    y: revenue
    color: seg
    style:
      color:
        categorical:
          {field}: {value}
"""


def _inline_named(field: str, value: str) -> str:
    return f"""
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 'A' AS seg, 100 AS revenue
rows:
  - revenue:
      type: bar
      query: q
      x: month
      y: revenue
      color: seg
      style:
        color:
          categorical:
            {field}: {value}
"""


def _inline_bare_gradient(value: str) -> str:
    return f"""
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 'A' AS seg, 1 AS v
rows:
  - type: heatmap
    query: q
    x: month
    y: seg
    color: v
    style:
      color:
        gradient:
          palette: {value}
"""


def _inline_named_gradient(value: str) -> str:
    return f"""
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 'A' AS seg, 1 AS v
rows:
  - h:
      type: heatmap
      query: q
      x: month
      y: seg
      color: v
      style:
        color:
          gradient:
            palette: {value}
"""


def test_inline_bare_chart_categorical_role_never_reaches_render():
    """The exact shape from the bug report: a role in a bare `rows:` chart
    must fail compile, not survive to render as UnknownPaletteError."""
    result = compile_board(_inline_bare("palette", "category"))
    assert not result.success
    assert result.errors[0].code == "ERR-PALETTE-UNKNOWN"
    assert result.board is None


def test_inline_named_chart_categorical_role_never_reaches_render():
    result = compile_board(_inline_named("palette", "category"))
    assert not result.success
    assert result.errors[0].code == "ERR-PALETTE-UNKNOWN"


def test_inline_bare_chart_single_series_role_never_reaches_render():
    result = compile_board(_inline_bare("single_series_palette", "category_ink"))
    assert not result.success
    assert result.errors[0].code == "ERR-PALETTE-UNKNOWN"


def test_inline_named_chart_single_series_role_never_reaches_render():
    result = compile_board(_inline_named("single_series_palette", "category_ink"))
    assert not result.success
    assert result.errors[0].code == "ERR-PALETTE-UNKNOWN"


def test_inline_bare_chart_gradient_role_never_reaches_render():
    result = compile_board(_inline_bare_gradient("sequence"))
    assert not result.success
    assert result.errors[0].code == "ERR-PALETTE-UNKNOWN"


def test_inline_named_chart_gradient_role_never_reaches_render():
    result = compile_board(_inline_named_gradient("sequence"))
    assert not result.success
    assert result.errors[0].code == "ERR-PALETTE-UNKNOWN"


def test_inline_bare_chart_typo_is_rejected_at_compile():
    """Before #7181, an inline chart typo was already a compile error. After
    #7181 (and before this guard), it silently passed compile — the second
    defect this task closes."""
    result = compile_board(_inline_bare("palette", "vivid-11"))
    assert not result.success
    assert result.errors[0].code == "ERR-PALETTE-UNKNOWN"


def test_inline_named_chart_typo_is_rejected_at_compile():
    result = compile_board(_inline_named("palette", "vivid-11"))
    assert not result.success
    assert result.errors[0].code == "ERR-PALETTE-UNKNOWN"


def test_inline_bare_chart_gradient_typo_is_rejected_at_compile():
    result = compile_board(_inline_bare_gradient("dbt-seq-bleu"))
    assert not result.success
    assert result.errors[0].code == "ERR-PALETTE-UNKNOWN"


def test_inline_named_chart_gradient_typo_is_rejected_at_compile():
    result = compile_board(_inline_named_gradient("dbt-seq-bleu"))
    assert not result.success
    assert result.errors[0].code == "ERR-PALETTE-UNKNOWN"


def test_inline_bare_chart_literal_palette_still_compiles():
    """A real palette file name, not a role, is unaffected by the guard."""
    result = compile_board(_inline_bare("palette", "vivid-10"))
    assert result.success, [e.message for e in result.errors]


def test_inline_named_chart_literal_gradient_still_compiles():
    result = compile_board(_inline_named_gradient("dbt-seq-blue"))
    assert result.success, [e.message for e in result.errors]
