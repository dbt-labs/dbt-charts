"""dark_companion_palette / dark_companion_stops — baked from label_ink(), not a lookup.

The three producers (board.py's board-level ChartStyleContext, _palette.py's
per-chart series_label, pie.py's per-chart slice labels) each derive their
parallel ink list as ``label_ink(mark, canvas)`` — canvas-aware, always a real
companion, never a fall-through to the mark color. Field names stay
(``dark_companion_palette`` / ``dark_companion_stops``): the resolved-schema
contract, not the derivation, is what's pinned here.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.colors import hex_to_oklch, is_light_canvas
from dbt_charts.core.compile import compile as df_compile
from dbt_charts.core.compile.config import (
    get_default_theme_name,
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.compile.resolve.style.palette import label_ink


def test_resolved_charts_style_dark_companion_palette_is_legible_ink_per_stop():
    """dark_companion_palette[i] is palette[i]'s own ink -- positionally
    aligned (not a scan result: exact equality to label_ink(palette[i],
    ...), which a permuted-but-still-legible tuple would fail), each
    clearing the contrast floor against the board's opaque canvas and
    differing from its mark."""
    from dbt_charts.core.colors import wcag_contrast

    ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    assert len(ctx.dark_companion_palette) == len(ctx.palette)
    for mark, ink in zip(ctx.palette, ctx.dark_companion_palette, strict=True):
        assert ink == label_ink(mark, ctx.ink_canvas)
        assert ink != mark
        assert wcag_contrast(ink, ctx.ink_canvas) >= 4.5 - 1e-6


class TestInlineUserPaletteGetsRealInk:
    """A board authoring an inline hex list (no companion file, no palette
    name at all) still gets legible, distinct label ink -- the companion
    lookup used to fall through to the mark color for exactly this case."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    def test_inline_palette_ink_clears_contrast_and_differs_from_mark(self):
        from dbt_charts.core.colors import wcag_contrast

        yaml_content = """
style:
  charts:
    color:
      categorical:
        palette: ["#4e79a7", "#f28e2b"]
rows:
  - text: hello
"""
        result = df_compile(yaml_content)
        assert result.board is not None
        ctx = result.board.chart_style_context
        assert ctx.palette == ["#4e79a7", "#f28e2b"]
        for mark, ink in zip(ctx.palette, ctx.dark_companion_palette, strict=True):
            assert ink != mark
            assert wcag_contrast(ink, ctx.ink_canvas) >= 4.5 - 1e-6


class TestMixedGrammarPaletteMembersEachGetRealInk:
    """With the palette-list gate fixed to route through parse_css_color
    (not the narrower is_sanitizable_color), a board can mix palette
    grammars in one list -- a CSS keyword name, an rgb() function, and an
    hsl() function -- and every stop gets real, canvas-aware ink."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    def test_each_shape_inks_to_label_ink_of_its_own_hex(self):
        yaml_content = """
theme: stark
style:
  charts:
    color:
      categorical:
        palette: ["white", "rgb(10,20,30)", "hsl(220, 60%, 50%)"]
rows:
  - text: hello
"""
        result = df_compile(yaml_content)
        assert result.errors == []
        assert result.board is not None
        ctx = result.board.chart_style_context
        assert ctx.palette == ["white", "rgb(10,20,30)", "hsl(220, 60%, 50%)"]
        expected_hex = ["#ffffff", "#0a141e", "#3366cc"]
        for mark, ink, hex_value in zip(
            ctx.palette, ctx.dark_companion_palette, expected_hex, strict=True
        ):
            assert ink == label_ink(hex_value, ctx.ink_canvas), (mark, ink, hex_value)


class TestNeonInkIsLighterThanMark:
    """On neon (near-black canvas), every vivid-10 stop's automatic label ink
    is lighter than its own mark -- the companion lookup returned
    vivid-10-dark, which is darker than the mark on a dark canvas."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    def test_neon_dark_companion_palette_is_lighter_than_every_mark(self):
        yaml_content = """
theme: neon
rows:
  - text: hello
"""
        result = df_compile(yaml_content)
        assert result.board is not None
        ctx = result.board.chart_style_context
        assert not is_light_canvas(ctx.background)
        for mark, ink in zip(ctx.palette, ctx.dark_companion_palette, strict=True):
            mark_l = hex_to_oklch(mark)[0]
            ink_l = hex_to_oklch(ink)[0]
            assert ink_l > mark_l, (mark, ink)


class TestChartLocalBackgroundInksAgainstItsOwnCanvas:
    """A chart authoring its own style.background inks its labels against
    THAT canvas, not the board's -- a sibling chart with no override still
    inks against the board canvas. Exercised on pie (dark_companion_stops)
    and on a line chart's series_label.dark_companion_palette, since both
    are independent producers of the same parallel-list pattern."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    _YAML = """
theme: stark
queries:
  q:
    columns: [cat, val1, val2]
    values:
      - [A, 40, 10]
      - [B, 60, 20]
charts:
  dark_pie:
    query: q
    type: pie
    theta: val1
    color: cat
    style:
      background: "#161616"
  light_pie:
    query: q
    type: pie
    theta: val1
    color: cat
  dark_line:
    query: q
    type: line
    x: cat
    y: [val1, val2]
    style:
      background: "#161616"
  light_line:
    query: q
    type: line
    x: cat
    y: [val1, val2]
rows:
  - dark_pie
  - light_pie
  - dark_line
  - light_line
"""

    def _resolve_all(self):
        result = df_compile(self._YAML)
        assert result.board is not None
        board = result.board
        ctx = board.chart_style_context
        data = [
            {"cat": "A", "val1": 40, "val2": 10},
            {"cat": "B", "val1": 60, "val2": 20},
        ]
        resolved = {
            name: resolve(chart, data, chart_style_context=ctx)
            for name, chart in board.charts.items()
        }
        return board, resolved

    def test_pie_inks_against_its_own_chart_local_background(self):
        board, resolved = self._resolve_all()
        dark_pie, light_pie = resolved["dark_pie"], resolved["light_pie"]
        assert dark_pie.dark_companion_stops != light_pie.dark_companion_stops
        for mark, ink in zip(
            dark_pie.palette, dark_pie.dark_companion_stops, strict=True
        ):
            assert hex_to_oklch(ink)[0] > hex_to_oklch(mark)[0], (mark, ink)
        assert light_pie.dark_companion_stops == tuple(
            label_ink(c, board.chart_style_context.ink_canvas)
            for c in light_pie.palette
        )

    def test_line_series_label_inks_against_its_own_chart_local_background(self):
        board, resolved = self._resolve_all()
        dark_line, light_line = resolved["dark_line"], resolved["light_line"]
        dark_ink = dark_line.style.series_label.dark_companion_palette
        light_ink = light_line.style.series_label.dark_companion_palette
        assert dark_ink != light_ink
        for mark, ink in zip(dark_line.palette, dark_ink, strict=True):
            assert hex_to_oklch(ink)[0] > hex_to_oklch(mark)[0], (mark, ink)


class TestNamedCssBackgroundResolvesToItsHex:
    """A board- or chart-level style.background naming a CSS keyword (`white`,
    not a hex) must ink labels against that keyword's real color -- the old
    fallback returned the mark unchanged because `is_sanitizable_color`
    doesn't recognize CSS keyword names, only hex + transparent/none."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    def test_charts_background_white_inks_against_ffffff(self):
        yaml_content = """
theme: stark
style:
  charts:
    background: white
rows:
  - text: hello
"""
        result = df_compile(yaml_content)
        assert result.board is not None
        ctx = result.board.chart_style_context
        assert ctx.background.lower() == "white"
        expected = tuple(label_ink(c, "#ffffff") for c in ctx.palette)
        assert tuple(ctx.dark_companion_palette) == expected
        # At least one stop must differ from its mark -- the old bug
        # returned the mark unchanged for every stop.
        assert any(
            ink != mark
            for mark, ink in zip(ctx.palette, ctx.dark_companion_palette, strict=True)
        )


class TestTransparentChartCanvasFallsThroughToBoard:
    """A chart authoring style.background: transparent has no opaque canvas
    of its own -- its label ink must fall through to the board's canvas,
    not crash trying to treat "transparent" as hex."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    def test_pie_transparent_background_inks_against_board_canvas(self):
        yaml_content = """
theme: stark
queries:
  q:
    columns: [cat, val]
    values:
      - [A, 40]
      - [B, 60]
charts:
  c:
    query: q
    type: pie
    theta: val
    color: cat
    style:
      background: transparent
rows:
  - c
"""
        result = df_compile(yaml_content)
        assert result.board is not None
        board = result.board
        chart = board.charts["c"]
        resolved = resolve(
            chart,
            [{"cat": "A", "val": 40}, {"cat": "B", "val": 60}],
            chart_style_context=board.chart_style_context,
        )
        board_canvas = board.chart_style_context.ink_canvas
        expected = tuple(label_ink(c, board_canvas) for c in resolved.palette)
        assert resolved.dark_companion_stops == expected


class TestTransparentBoardCanvasFallsThroughToTheTheme:
    """A board-level style.background: transparent has no opaque color of
    its own -- ink_canvas() composites it over the THEME's own canvas
    (base.background in resolve_style_and_context), so it now inks against
    the theme, not against a raised error. Every built-in theme's own
    canvas is opaque, so this never actually raises in practice -- see
    TestNoOpaqueCanvasAnywhereRaises for the one case that does."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    def test_transparent_board_background_inks_against_the_theme_canvas(self):
        theme_background = get_theme_style(get_default_theme_name()).background
        yaml_content = """
style:
  background: transparent
rows:
  - text: hello
"""
        result = df_compile(yaml_content)
        assert result.errors == []
        assert result.board is not None
        ctx = result.board.chart_style_context
        assert ctx.background.lower() == "transparent"
        assert ctx.ink_canvas.lower() == theme_background.lower()
        for mark, ink in zip(ctx.palette, ctx.dark_companion_palette, strict=True):
            assert ink == label_ink(mark, theme_background)


class TestNoOpaqueCanvasAnywhereRaises:
    """When even the THEME's own canvas is translucent -- not reachable
    through normal board authoring, only by patching the compiled theme
    Style directly -- ink_canvas() has nothing opaque anywhere in the
    stack to composite down to, and raises a clear, author-facing error
    instead of silently assuming white."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    def test_translucent_theme_canvas_raises_clear_error(self, model_copy_at):
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        translucent_theme = model_copy_at(
            get_theme_style(get_default_theme_name()),
            "background",
            "rgba(0, 0, 0, 0.5)",
        )
        with pytest.raises(ValueError, match=r"theme canvas must be opaque"):
            resolve_chart_style_context(translucent_theme)


class TestNestedSectionTranslucentBackgroundComposites:
    """The nested-layouts shape: a board section (nested board item, not a
    chart) authors a translucent style.background (rgba(...)) -- the same
    grammar the playground/nested-layouts fixture uses for card tinting. A
    chart inside that section inks against the section's own translucent
    fill composited over the board's opaque canvas, not against the raw
    board canvas and not by crashing on the rgba(...) string."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    _YAML = """
theme: stark
queries:
  q:
    columns: [cat, val]
    values:
      - [A, 40]
      - [B, 60]
charts:
  c:
    query: q
    type: pie
    theta: val
    color: cat
rows:
  - style:
      background: "rgba(102, 126, 234, 0.1)"
    cols:
      - c
"""

    def test_pie_in_a_tinted_section_inks_against_the_composited_canvas(self):
        from dbt_charts.core.colors import wcag_contrast

        result = df_compile(self._YAML)
        assert result.errors == []
        assert result.board is not None
        board = result.board
        section = board.layout.items[0].board
        assert section is not None
        section_canvas = section.chart_style_context.ink_canvas
        assert (
            section_canvas.lower() != "#ffffff"
        )  # actually tinted, not just passed through

        chart = section.charts["c"]
        resolved = resolve(
            chart,
            [{"cat": "A", "val": 40}, {"cat": "B", "val": 60}],
            chart_style_context=section.chart_style_context,
        )
        # An authored translucent background composites exactly once over
        # the canvas beneath it. The chart authors no style.background of
        # its own, so it only INHERITS the section's background -- adding
        # nothing -- and its ink canvas is exactly the section's own
        # already-composited ink_canvas, not that same tint re-applied a
        # second time.
        chart_canvas = section_canvas

        for mark, ink in zip(
            resolved.palette, resolved.dark_companion_stops, strict=True
        ):
            assert ink == label_ink(mark, chart_canvas)
            assert wcag_contrast(ink, chart_canvas) >= 4.5 - 1e-6


class TestNestedSectionTranslucentBackgroundOnNeonComposites:
    """Same tinted-section shape, on neon -- the composited canvas still
    leans dark (a light 10% tint over near-black stays near-black), so ink
    still reads lighter than the mark, same as any other neon canvas."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    _YAML = """
theme: neon
queries:
  q:
    columns: [cat, val]
    values:
      - [A, 40]
      - [B, 60]
charts:
  c:
    query: q
    type: pie
    theta: val
    color: cat
rows:
  - style:
      background: "rgba(255, 255, 255, 0.08)"
    cols:
      - c
"""

    def test_pie_in_a_tinted_section_on_neon_inks_lighter_than_mark(self):
        result = df_compile(self._YAML)
        assert result.errors == []
        assert result.board is not None
        board = result.board
        section = board.layout.items[0].board
        assert section is not None
        composited = section.chart_style_context.ink_canvas
        # A light 10% tint over near-black stays near-black (still dark),
        # but composites to a genuinely different value than the board's
        # own uncomposited canvas -- neither side pins the theme's exact
        # default hex, which is free to retune.
        assert not is_light_canvas(composited)
        assert composited != board.chart_style_context.ink_canvas

        chart = section.charts["c"]
        resolved = resolve(
            chart,
            [{"cat": "A", "val": 40}, {"cat": "B", "val": 60}],
            chart_style_context=section.chart_style_context,
        )
        for mark, ink in zip(
            resolved.palette, resolved.dark_companion_stops, strict=True
        ):
            assert hex_to_oklch(ink)[0] > hex_to_oklch(mark)[0], (mark, ink)


class TestNestedSectionCompositesOverTheParentsPaintedCanvasNotTheThemes:
    """A nested board (a section) derives ink against what is genuinely
    painted beneath it -- the PARENT scope's own composited canvas -- not
    the raw theme canvas underneath that. A board overriding style.background
    paints a different canvas than its theme's own default, and a tinted
    section nested inside it must composite over the board's real canvas."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    _YAML = """
theme: stark
style:
  background: "#161616"
queries:
  q:
    columns: [cat, val]
    values:
      - [A, 40]
      - [B, 60]
charts:
  c:
    query: q
    type: pie
    theta: val
    color: cat
rows:
  - style:
      background: "rgba(255, 255, 255, 0.08)"
    cols:
      - c
"""

    def test_section_ink_canvas_composites_over_the_boards_real_canvas(self):
        from dbt_charts.core.colors import wcag_contrast

        result = df_compile(self._YAML)
        assert result.errors == []
        assert result.board is not None
        board = result.board
        # The root's own canvas is its override, opaque -- unchanged by any
        # section beneath it.
        assert board.chart_style_context.ink_canvas == "#161616"

        section = board.layout.items[0].board
        assert section is not None
        section_canvas = section.chart_style_context.ink_canvas
        # Hand-composited: rgba(255, 255, 255, 0.08) over the board's own
        # canvas (#161616), not over stark's raw theme canvas (#ffffff).
        assert section_canvas == "#292929"

        chart = section.charts["c"]
        resolved = resolve(
            chart,
            [{"cat": "A", "val": 40}, {"cat": "B", "val": 60}],
            chart_style_context=section.chart_style_context,
        )
        for mark, ink in zip(
            resolved.palette, resolved.dark_companion_stops, strict=True
        ):
            assert wcag_contrast(ink, section_canvas) >= 4.5 - 1e-6, (mark, ink)


class TestNestedSectionInheritingAnUnauthoredBackgroundDoesNotDoubleComposite:
    """An authored translucent background composites exactly once over the
    canvas beneath it. A scope that only INHERITS a background (authors no
    style.background of its own, whether it authors some other unrelated
    style key or nothing at all) adds no new composite -- its ink canvas is
    its parent's, verbatim. Only a scope that authors its OWN background
    earns one more composite on top."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    _YAML = """
theme: neon
style:
  background: "rgba(255, 255, 255, 0.08)"
rows:
  - text: unauthored
  - style:
      gap: 12
    text: gap-only
  - style:
      background: "rgba(255, 255, 255, 0.08)"
    text: authored
"""

    def test_inheriting_sections_share_the_roots_canvas_authoring_one_composites_again(
        self,
    ):
        result = df_compile(self._YAML)
        assert result.errors == []
        assert result.board is not None
        board = result.board
        root_canvas = board.chart_style_context.ink_canvas
        # Hand-composited: rgba(255, 255, 255, 0.08) over neon's own raw
        # (near-black) canvas.
        assert root_canvas == "#292929"

        unauthored, gap_only, authored = (
            item.board.chart_style_context.ink_canvas for item in board.layout.items
        )
        # Neither an unauthored section nor one authoring an unrelated key
        # (gap) triggers a new composite -- both inherit the root's own
        # canvas verbatim, not `rgba(...)` re-applied over it a second time.
        assert unauthored == root_canvas
        assert gap_only == root_canvas
        # A section authoring its OWN background composites exactly one
        # more layer on top of the root's already-composited canvas.
        assert authored == "#3a3a3a"
        assert authored != root_canvas


class TestNestedSectionOwnThemeComposesOverItsOwnCanvasNotTheParents:
    """A nested board that declares its own `theme:` paints on top of THAT
    theme's own canvas -- not the parent's, which reflects a different
    theme's colors entirely. A stark root with a nested `theme: neon`
    section (no authored background of its own) must derive ink against
    neon's own near-black default, not the parent's white."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    _YAML = """
theme: stark
rows:
  - theme: neon
    text: hello
"""

    def test_section_ink_canvas_is_neons_own_canvas_ink_reads_lighter_than_mark(self):
        result = df_compile(self._YAML)
        assert result.errors == []
        assert result.board is not None
        board = result.board
        root_canvas = board.chart_style_context.ink_canvas
        assert root_canvas == get_theme_style("stark").background.lower()

        section = board.layout.items[0].board
        assert section is not None
        section_canvas = section.chart_style_context.ink_canvas
        assert section_canvas == get_theme_style("neon").background.lower()
        assert section_canvas != root_canvas

        for mark, ink in zip(
            section.chart_style_context.palette,
            section.chart_style_context.dark_companion_palette,
            strict=True,
        ):
            assert hex_to_oklch(ink)[0] > hex_to_oklch(mark)[0], (mark, ink)


class TestNestedSectionChartsBackgroundAuthoredVsInherited:
    """`background_authored`'s explicit `style.charts.background:` arm,
    at nested scope: a section authoring one composites once more; a
    section authoring `style.charts.background: null` -- the key present,
    the value None -- is not authored, and inherits the parent's canvas
    unchanged."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    _YAML = """
theme: neon
style:
  background: "rgba(255, 255, 255, 0.08)"
rows:
  - style:
      charts:
        background: "rgba(255, 255, 255, 0.08)"
    text: composited-again
  - style:
      charts:
        background: null
    text: inherits
"""

    def test_authored_charts_background_composites_null_inherits_unchanged(self):
        result = df_compile(self._YAML)
        assert result.errors == []
        assert result.board is not None
        board = result.board
        root_canvas = board.chart_style_context.ink_canvas
        # Hand-composited: rgba(255, 255, 255, 0.08) over neon's own raw canvas.
        assert root_canvas == "#292929"

        composited_again, inherits = (
            item.board.chart_style_context.ink_canvas for item in board.layout.items
        )
        # One more composite on top of the root's already-composited canvas.
        assert composited_again == "#3a3a3a"
        assert composited_again != root_canvas
        # `background: null` sets the key but not a color -- not authored,
        # so this section inherits the root's canvas verbatim, not a third
        # composite of the same tint over it.
        assert inherits == root_canvas


class TestNestedSectionOwnThemeStillComposesOverTheParentsRealCanvas:
    """render always paints a nested board's background over the pixels its
    parent actually composited -- own `theme:` or not; the fact that a
    scope's own theme differs from its parent's only forces a fresh
    composite (nothing to reuse verbatim across a theme switch), it never
    changes WHAT is composited against. A stark root that authors its own
    translucent tint, and a nested `theme: neon` section, in three authoring
    variants: (a) and (b) inherit the root's tint like any other cascading
    style field (a theme switch doesn't wall off ordinary inheritance) and
    that inherited tint gets its own, fresh composite over the root's real
    canvas since the theme switch means nothing can be reused verbatim; (c)
    authors its own background outright, which replaces the inherited value
    rather than stacking with it, then composites that instead."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    _ROOT = """
theme: stark
style:
  background: "rgba(0, 0, 0, 0.10)"
rows:
"""

    def _compile(self, section_yaml: str):
        result = df_compile(self._ROOT + "  - " + section_yaml)
        assert result.errors == [], result.errors
        assert result.board is not None
        return result.board

    def _assert_canvas_and_contrast(self, board, expected_canvas: str):
        root_canvas = board.chart_style_context.ink_canvas
        # Hand-composited: rgba(0, 0, 0, 0.10) over stark's white.
        assert root_canvas == "#e6e6e6"

        section = board.layout.items[0].board
        assert section is not None
        section_canvas = section.chart_style_context.ink_canvas
        assert section_canvas == expected_canvas

        from dbt_charts.core.colors import wcag_contrast

        for mark, ink in zip(
            section.chart_style_context.palette,
            section.chart_style_context.dark_companion_palette,
            strict=True,
        ):
            assert wcag_contrast(ink, section_canvas) >= 4.5 - 1e-6, (mark, ink)

    def test_section_authoring_nothing_inherits_the_tint_and_composites_fresh(self):
        board = self._compile("theme: neon\n    text: hello")
        # Hand-composited: the tint this section inherits from the root
        # (rgba(0, 0, 0, 0.10), the same value neon's own charts.background
        # falls back to since it authors none itself) over the root's own
        # already-composited canvas (#e6e6e6) -- a fresh paint event since
        # the theme switch means nothing here can be reused verbatim.
        self._assert_canvas_and_contrast(board, "#cfcfcf")

    def test_section_authoring_only_an_unrelated_field_composites_the_same_way(self):
        """Authoring an unrelated field (`layout.rows.gap:`) still forces
        `board_style` to not be `None` -- the compositing math is identical
        to authoring nothing, exercised here to pin the *other* code
        branch (`own_patch`, not the `board_style is None` fast path)."""
        board = self._compile(
            "theme: neon\n    style:\n      layout:\n        rows:\n"
            "          gap: 12\n    text: hello"
        )
        self._assert_canvas_and_contrast(board, "#cfcfcf")

    def test_section_authoring_its_own_background_replaces_the_inherited_tint(self):
        board = self._compile(
            'theme: neon\n    style:\n      background: "rgba(255, 255, 255, 0.08)"'
            "\n    text: hello"
        )
        # The section's own authored background overrides (not stacks with)
        # what it would have inherited from the root -- hand-composited:
        # rgba(255, 255, 255, 0.08) over the root's own canvas (#e6e6e6).
        self._assert_canvas_and_contrast(board, "#e8e8e8")


class TestNestedSectionOwnThemeStillFoldsAncestorPatches:
    """The `board_style is None` branch's fresh resolve folds `*patches`
    (whatever an ancestor authored) onto the new theme's base, same as the
    `own_patch` branch does for a scope that authors something itself --
    without the fold, a nested own-theme section would silently drop
    everything an ancestor authored the instant it named its own theme.
    `font.color` isn't background-compositing machinery, so it pins the
    fold itself, not the composite math the sibling test class covers."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    _YAML = """
theme: neon
style:
  font:
    color: "#ff00ff"
rows:
  - theme: stark
    text: hello
"""

    def test_section_resolves_the_roots_font_color_and_its_own_ink_canvas(self):
        result = df_compile(self._YAML)
        assert result.errors == [], result.errors
        assert result.board is not None
        board = result.board
        assert (
            board.chart_style_context.ink_canvas
            == get_theme_style("neon").background.lower()
        )

        section = board.layout.items[0].board
        assert section is not None
        # Folded from the root's own patch -- deleting the `*patches` fold
        # would leave this at stark's own theme default instead.
        assert section.resolved_style.font.color == "#ff00ff"
        # No background authored anywhere -- neon's canvas composites to
        # itself, so does stark's; the parent's tint (none here) is moot.
        assert (
            section.chart_style_context.ink_canvas
            == get_theme_style("stark").background.lower()
        )
