"""Regression tests for normalizer.normalize_board and Board.set_theme.

board.level counts titled ancestors, not structural nesting depth.
Bare cols/rows wrappers with no title pass through transparently.
Author override: board.style.title.level = <int> locks the board's level.

Board.set_theme is the supported API for mutating board.theme after compile.
It re-cascades resolved_style for the board and every nested board, so render
can read resolved_style without re-checking. Direct ``board.theme = …``
writes leave resolved_style stale.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.normalize.dispatch import normalize_board
from dbt_charts.core.diagnostics.codes_compile import ERR_VALIDATION_FIELD

_NESTED_YAML = """\
title: Root
cols:
  - title: Left
    text: "left panel"
  - title: Right
    text: "right panel"
"""


def _compile_nested():
    result = compile(_NESTED_YAML)
    assert result.success, result.errors
    assert result.board is not None
    return result.board


class TestSemanticBoardLevel:
    """board.level = count of titled ancestors, not structural depth."""

    def test_titled_root_level_is_1(self):
        """Root board with a title is level=1."""
        board = normalize_board(
            AuthoredBoard.model_validate({"title": "Root", "text": "hello"})
        )
        assert board.level == 1

    def test_untitled_root_level_is_0(self):
        """Root board with no title is level=0 (no heading at this position)."""
        board = normalize_board(
            AuthoredBoard.model_validate(
                {
                    "rows": [{"title": "Section", "text": "hello"}],
                }
            )
        )
        assert board.level == 0

    def test_bare_wrapper_does_not_increment_level(self):
        """A bare cols wrapper (no title) must not bump level.

        dundersign-shape: titled root → bare cols → chart.
        chart.board_level must be 1, not 2.
        """
        board = normalize_board(
            AuthoredBoard.model_validate(
                {
                    "title": "Root",
                    "cols": [
                        {
                            # bare wrapper — no title
                            "rows": [{"title": "Section", "text": "hello"}],
                        }
                    ],
                }
            )
        )
        # Root is level=1 (has title)
        assert board.level == 1
        # Bare cols wrapper is level=1 (no title → doesn't bump)
        cols_item = board.layout.items[0]
        assert cols_item.board is not None
        bare_wrapper = cols_item.board
        assert bare_wrapper.level == 1
        # Section inside bare wrapper is level=2 (first titled descendant under root)
        section_item = bare_wrapper.layout.items[0]
        assert section_item.board is not None
        assert section_item.board.level == 2

    def test_dundersign_shape_wrapper_levels(self):
        """Titled root → bare cols wrapper → bare rows wrapper.

        This is the exact dundersign bug shape. Both bare wrappers must be
        level=1 (inheriting from the titled root) so that charts inside them
        receive board_level=1 and their titles render at sizes[1] (H2), not
        the previous wrong sizes[3] (H4).
        """
        board = normalize_board(
            AuthoredBoard.model_validate(
                {
                    "title": "Commercial finance",
                    "cols": [
                        {
                            # bare cols wrapper — no title
                            "rows": [
                                {
                                    # bare rows wrapper — no title
                                    "text": "placeholder content",
                                },
                            ],
                        }
                    ],
                }
            )
        )
        # Root has title → level=1
        assert board.level == 1

        # Walk down: board → cols_item.board → rows_item.board
        cols_item = board.layout.items[0]
        assert cols_item.board is not None, "expected board item for bare cols wrapper"
        bare_cols = cols_item.board
        assert bare_cols.level == 1, (
            f"bare cols wrapper should be level=1, got {bare_cols.level}"
        )

        rows_item = bare_cols.layout.items[0]
        assert rows_item.board is not None, "expected board item for bare rows wrapper"
        bare_rows = rows_item.board
        assert bare_rows.level == 1, (
            f"bare rows wrapper should be level=1, got {bare_rows.level}"
        )

    def test_untitled_root_first_titled_child_is_level_1(self):
        """Untitled root (level=0) → titled child is level=1."""
        board = normalize_board(
            AuthoredBoard.model_validate(
                {
                    "rows": [
                        {"title": "First Section", "text": "hello"},
                    ]
                }
            )
        )
        assert board.level == 0
        child = board.layout.items[0].board
        assert child is not None
        assert child.level == 1

    def test_titled_nested_under_titled_is_level_2(self):
        """Titled root → titled child is level=2 (two titled ancestors)."""
        board = normalize_board(
            AuthoredBoard.model_validate(
                {
                    "title": "Dashboard",
                    "rows": [
                        {"title": "Section A", "text": "hello"},
                    ],
                }
            )
        )
        assert board.level == 1
        child = board.layout.items[0].board
        assert child is not None
        assert child.level == 2

    def test_double_bare_wrapper_preserves_parent_level(self):
        """Two bare wrappers in a row: titled root → bare → bare → titled child.

        The titled child is at level=2 (only one titled ancestor: the root).
        """
        board = normalize_board(
            AuthoredBoard.model_validate(
                {
                    "title": "Root",
                    "cols": [
                        {
                            # first bare wrapper
                            "rows": [
                                {
                                    # second bare wrapper
                                    "cols": [
                                        {"title": "Deep Section", "text": "hello"},
                                    ],
                                }
                            ],
                        }
                    ],
                }
            )
        )
        # Walk the tree
        first_bare = board.layout.items[0].board
        assert first_bare is not None
        assert first_bare.level == 1  # no title, inherits root's level

        second_bare = first_bare.layout.items[0].board
        assert second_bare is not None
        assert second_bare.level == 1  # still no title

        deep_section = second_bare.layout.items[0].board
        assert deep_section is not None
        # Titled, and only one titled ancestor (root), so level=2
        assert deep_section.level == 2


class TestSemanticBoardLevelTabs:
    """Tab bodies inherit semantic level from their parent.

    Regression for the bug where content-only and empty tab branches in
    `_resolve_tab_items` constructed `Board(...)` directly without threading
    `parent_level`, so their level defaulted to 0 and titles rendered at H1.
    """

    def test_content_only_tabs_inherit_parent_level(self):
        """Titled root → titled tabs container → titled content-only tab.

        The titled tabs board is the parent of each tab body, and each tab title
        adds one more titled ancestor — so a content-only tab under a titled
        root is level=2 (root=1, tab=2).
        """
        board = normalize_board(
            AuthoredBoard.model_validate(
                {
                    "title": "Root",
                    "tabs": {
                        "items": [
                            {"title": "Tab A", "text": "alpha"},
                            {"title": "Tab B", "text": "beta"},
                        ],
                    },
                }
            )
        )
        assert board.level == 1
        tab_a = board.layout.items[0].board
        tab_b = board.layout.items[1].board
        assert tab_a is not None and tab_b is not None
        # Regression: previously these defaulted to level=0 and rendered at H1.
        assert tab_a.level == 2, (
            f"content-only tab A level should be 2, got {tab_a.level}"
        )
        assert tab_b.level == 2, (
            f"content-only tab B level should be 2, got {tab_b.level}"
        )

    def test_empty_tabs_inherit_parent_level(self):
        """Titled root → titled empty tab → level=2."""
        board = normalize_board(
            AuthoredBoard.model_validate(
                {
                    "title": "Root",
                    "tabs": {"items": [{"title": "Empty Tab"}]},
                }
            )
        )
        assert board.level == 1
        empty_tab = board.layout.items[0].board
        assert empty_tab is not None
        assert empty_tab.level == 2

    def test_content_only_tab_with_explicit_rows_null_is_not_misrouted(self):
        """`rows: null` beside `text:` must not flip a content-only tab to nested.

        `_resolve_tab_items` dumps the tab with `exclude_unset=True` (needed to
        preserve an authored `style.formats: null`), which can leave `rows`
        present in the dict with an explicit `None` value. The nested-board
        detection has to check the value, not just key presence, or an author
        who explicitly nulls a layout key on a content-only tab gets an
        (empty) nested board instead of their text.
        """
        board = normalize_board(
            AuthoredBoard.model_validate(
                {
                    "title": "Root",
                    "tabs": {
                        "items": [{"title": "Tab A", "text": "alpha", "rows": None}],
                    },
                }
            )
        )
        tab_a = board.layout.items[0].board
        assert tab_a is not None
        assert tab_a.text == "alpha"
        assert tab_a.level == 2

    def test_deeply_nested_content_tabs(self):
        """Titled root → titled section → titled content-only tab → level=3.

        Exercises the case the reviewer flagged in the quick-guide demo: a
        layout board sitting between root and the tabs, so the tab body is
        three titled ancestors deep.
        """
        board = normalize_board(
            AuthoredBoard.model_validate(
                {
                    "title": "Dashboard",
                    "rows": [
                        {
                            "title": "Layout Types",
                            "tabs": {
                                "items": [
                                    {"title": "Tab A", "text": "alpha"},
                                ],
                            },
                        }
                    ],
                }
            )
        )
        assert board.level == 1
        section = board.layout.items[0].board
        assert section is not None
        assert section.level == 2
        tab_a = section.layout.items[0].board
        assert tab_a is not None
        assert tab_a.level == 3, f"deep tab A level should be 3, got {tab_a.level}"


class TestTabItemOwnStyle:
    """Content-only and empty tab items resolve their own `style:` block.

    Regression for the bug where those branches in `_resolve_tab_items`
    constructed `Board(...)` with the parent's resolved_style/chart_style_context
    verbatim, never reading `tab_item.style`. The nested-board branch already
    gets this for free via `normalize_board` → `compile_board_resolved_style`.
    """

    def test_content_only_tab_resolves_own_style(self):
        """A content-only tab authoring style.font.color gets its own value."""
        board = normalize_board(
            AuthoredBoard.model_validate(
                {
                    "title": "Root",
                    "tabs": {
                        "items": [
                            {
                                "title": "Tab A",
                                "text": "alpha",
                                "style": {"font": {"color": "#ff00ff"}},
                            },
                        ],
                    },
                }
            )
        )
        assert board.resolved_style.font.color != "#ff00ff"
        tab_a = board.layout.items[0].board
        assert tab_a is not None
        assert tab_a.resolved_style.font.color == "#ff00ff"

    def test_empty_tab_resolves_own_style(self):
        """An empty tab authoring style.font.color gets its own value."""
        board = normalize_board(
            AuthoredBoard.model_validate(
                {
                    "title": "Root",
                    "tabs": {
                        "items": [
                            {
                                "title": "Empty Tab",
                                "style": {"font": {"color": "#ff00ff"}},
                            },
                        ],
                    },
                }
            )
        )
        assert board.resolved_style.font.color != "#ff00ff"
        empty_tab = board.layout.items[0].board
        assert empty_tab is not None
        assert empty_tab.resolved_style.font.color == "#ff00ff"


class TestAuthorLevelOverride:
    """board.style.title.level: <int> overrides the semantic computation."""

    def test_author_override_wins_over_semantic(self):
        """style.title.level: 3 on a root board forces level=3."""
        board = normalize_board(
            AuthoredBoard.model_validate(
                {
                    "title": "Root",
                    "text": "hello",
                    "style": {"title": {"level": 3}},
                }
            )
        )
        # Semantic would give level=1, but override says 3
        assert board.level == 3

    def test_override_propagates_to_descendants(self):
        """When a board sets level=3, titled children compute level=4."""
        board = normalize_board(
            AuthoredBoard.model_validate(
                {
                    "title": "Root",
                    "style": {"title": {"level": 3}},
                    "rows": [
                        {"title": "Child Section", "text": "hello"},
                    ],
                }
            )
        )
        assert board.level == 3
        child = board.layout.items[0].board
        assert child is not None
        # Child has a title, parent_level=3, so child.level=4
        assert child.level == 4

    def test_override_with_bare_wrapper_child(self):
        """Override on titled root → bare child inherits the override level."""
        board = normalize_board(
            AuthoredBoard.model_validate(
                {
                    "title": "Root",
                    "style": {"title": {"level": 2}},
                    "cols": [
                        {
                            # bare wrapper, inherits parent_level=2
                            "text": "content",
                        }
                    ],
                }
            )
        )
        assert board.level == 2
        bare_child = board.layout.items[0].board
        assert bare_child is not None
        # Bare child (no title) inherits parent_level=2, no increment
        assert bare_child.level == 2

    def test_level_above_ramp_rejected(self):
        """style.title.level beyond the sizes ramp must raise at compile."""
        import pytest

        with pytest.raises(ValueError, match=r"exceeds the H-ramp"):
            normalize_board(
                AuthoredBoard.model_validate(
                    {
                        "title": "Root",
                        "text": "hi",
                        # default ramp has 6 entries (H1–H6); 7 is out of range
                        "style": {"title": {"level": 7}},
                    }
                )
            )


# ---------------------------------------------------------------------------
# Board.set_theme — atomic theme mutation + cascade
# ---------------------------------------------------------------------------


class TestBoardSetTheme:
    """Board.set_theme is the only supported way to change a board's theme.

    Direct ``board.theme = …`` writes are unsupported because the render layer
    trusts ``resolved_style`` to reflect ``theme`` without re-checking.
    """

    def test_set_theme_recomputes_resolved_style(self):
        """set_theme changes the resolved_style background when switching to a
        provably different theme (clarity vs neon have different backgrounds)."""
        board = _compile_nested()
        board.set_theme("clarity")
        clarity_bg = board.resolved_style.background

        board.set_theme("neon")

        assert board.resolved_style.background != clarity_bg

    def test_set_theme_on_root_propagates_to_nested_boards(self):
        """Root.set_theme must update every nested board's resolved_style.

        Nested boards' own ``board.theme`` values stay at the compile-time
        default; the cascade still has to re-run on them since the theme
        base underneath every one of them just changed.
        """
        board = _compile_nested()
        nested_left = board.layout.items[0].board
        nested_right = board.layout.items[1].board
        assert nested_left is not None
        assert nested_right is not None

        original_left_style = nested_left.resolved_style
        original_right_style = nested_right.resolved_style

        board.set_theme("neon")

        assert nested_left.resolved_style is not original_left_style
        assert nested_right.resolved_style is not original_right_style

    def test_set_theme_on_nested_board_recomputes_subtree(self):
        """A nested board's set_theme re-cascades that subtree only.

        The nested board has its own authored_style block — the cascade output
        must reflect (new theme base × that authored_style).
        """
        result = compile(
            """\
title: Root
cols:
  - title: Left
    text: "left panel"
    style:
      background: "#eeeeee"
  - title: Right
    text: "right panel"
"""
        )
        assert result.success, result.errors
        board = result.board
        assert board is not None

        nested_board = board.layout.items[0].board
        assert nested_board is not None
        assert nested_board.authored_style is not None
        original_nested_style = nested_board.resolved_style

        nested_board.set_theme("neon")

        assert nested_board.resolved_style != original_nested_style

    def test_set_theme_on_root_preserves_authored_color_on_styled_nested_board(self):
        """Root.set_theme's re-cascade must thread the root's own authored style
        into a nested board that authors its own, unrelated style patch — the
        same contribution the initial compile gives it."""
        result = compile(
            """\
title: Root
style:
  font:
    color: "#a1a1a1"
cols:
  - title: Left
    text: "left panel"
    style:
      background: "#eeeeee"
  - title: Right
    text: "right panel"
"""
        )
        assert result.success, result.errors
        board = result.board
        assert board is not None

        nested_board = board.layout.items[0].board
        assert nested_board is not None
        assert nested_board.resolved_style.font.color == "#a1a1a1"

        board.set_theme("neon")

        assert nested_board.resolved_style.font.color == "#a1a1a1"

    def test_direct_theme_write_does_not_recompute(self):
        """Pins the regression contract that direct .theme writes are unsupported.

        Writing ``board.theme = X`` leaves ``resolved_style`` stale; the render
        layer treats this as a programmer error rather than a state to
        recover from. ``Board.set_theme`` is the supported API.
        """
        board = _compile_nested()
        original_style = board.resolved_style

        board.theme = "dark"

        assert board.resolved_style is original_style


class TestNormalizeChartWiring:
    """STEP 4 populates board.charts for all named charts.

    charts_v2 is computed from the same authored chart_def/query_registry as
    the flat Chart — a second, independent normalization, not derived from the
    flat model. See h1-wire-normalize-chart-v2-delete-flat-to-normalized.
    """

    _YAML = """\
title: Root
queries:
  q:
    sql: SELECT month, revenue FROM t
    source: test
charts:
  bar1:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - bar1
"""

    def test_charts_v2_populated_on_board(self):
        result = compile(self._YAML)
        assert result.success, result.errors
        board = result.board
        assert board is not None
        assert "bar1" in board.charts
        assert board.charts["bar1"].type == "bar"


class TestStyleFrameKey:
    """``style.board:`` was renamed to ``style.frame:`` (``BoardStyle`` -> ``FrameStyle``).

    Pre-launch, no compat alias: the old key must be rejected outright by
    ``extra="forbid"``, not silently accepted alongside the new one.
    """

    def test_style_frame_key_accepted(self):
        board = normalize_board(
            AuthoredBoard.model_validate(
                {"style": {"frame": {"width": 900}}, "text": "hello"}
            )
        )
        assert board.resolved_style.frame.width == 900.0

    def test_style_board_key_rejected(self):
        with pytest.raises(ValidationError):
            AuthoredBoard.model_validate(
                {"style": {"board": {"width": 900}}, "text": "hello"}
            )


class TestRootBoardWidth:
    """Root-level ``width:`` is sugar for ``style.frame.width`` — same effect.

    ``width:`` is normally read only when a board is nested (LayoutItem.user_width
    in normalize/layout.py); on the root board it must set the board's own width
    instead, since there is no parent layout to place it into.
    """

    def test_root_width_sets_board_width(self):
        board = normalize_board(
            AuthoredBoard.model_validate(
                {"width": 900, "text": "hello"},
            )
        )
        assert board.resolved_style.frame.width == 900.0

    def test_root_width_px_string_sets_board_width(self):
        board = normalize_board(
            AuthoredBoard.model_validate(
                {"width": "900px", "text": "hello"},
            )
        )
        assert board.resolved_style.frame.width == 900.0

    def test_root_width_percent_is_rejected(self):
        from dbt_charts.core.compile.errors import CompilationError

        with pytest.raises(CompilationError, match="width") as exc:
            normalize_board(
                AuthoredBoard.model_validate(
                    {"width": "50%", "text": "hello"},
                )
            )
        assert exc.value.code is ERR_VALIDATION_FIELD

    def test_root_width_unparseable_is_rejected(self):
        from dbt_charts.core.compile.errors import CompilationError

        with pytest.raises(CompilationError, match="not a valid dimension") as exc:
            normalize_board(
                AuthoredBoard.model_validate(
                    {"width": "wide", "text": "hello"},
                )
            )
        assert exc.value.code is ERR_VALIDATION_FIELD

    def test_root_width_zero_is_rejected(self):
        from dbt_charts.core.compile.errors import CompilationError

        with pytest.raises(CompilationError, match="positive") as exc:
            normalize_board(
                AuthoredBoard.model_validate(
                    {"width": 0, "text": "hello"},
                )
            )
        assert exc.value.code is ERR_VALIDATION_FIELD

    def test_root_width_conflicting_with_board_width_style_is_rejected(self):
        from dbt_charts.core.compile.errors import CompilationError

        with pytest.raises(CompilationError, match="Cannot specify both") as exc:
            normalize_board(
                AuthoredBoard.model_validate(
                    {
                        "width": 900,
                        "style": {"frame": {"width": 700}},
                        "text": "hello",
                    }
                )
            )
        assert exc.value.code is ERR_VALIDATION_FIELD
        assert "style.frame.width" in str(exc.value)

    def test_root_width_survives_set_theme(self):
        """width: sugar must survive set_theme like style.frame.width does."""
        board = normalize_board(
            AuthoredBoard.model_validate({"width": 900, "text": "hello"})
        )
        assert board.resolved_style.frame.width == 900.0
        board.set_theme("paper")
        assert board.resolved_style.frame.width == 900.0

    def test_nested_board_width_still_means_layout_width_not_board_width(self):
        """A nested board's width: is layout placement, untouched by this fix."""
        result = compile(
            "title: Root\n"
            "cols:\n"
            "  - title: Left\n"
            '    width: "30%"\n'
            "    text: left\n"
            "  - title: Right\n"
            "    text: right\n"
        )
        assert result.success, result.errors
        board = result.board
        assert board is not None
        left_item = board.layout.items[0]
        assert left_item.user_width == "30%"
        assert left_item.board is not None
        assert (
            left_item.board.resolved_style.frame.width
            == board.resolved_style.frame.width
        )


# ---------------------------------------------------------------------------
# style.background is not inherited into nested boards (CSS background-color
# semantics)
# ---------------------------------------------------------------------------


class TestNestedBoardBackgroundIsNotInherited:
    """style.background is CSS background-color: not inherited. A nested
    board that authors no background of its own paints nothing of its own
    -- render's own wrapper rect for it is transparent, not a second copy
    of the parent's translucent tint (which would visually double the
    alpha) -- and its ink canvas (used for text/contrast) is its parent's,
    via the compile-time fast path that reuses the parent's resolved_style
    object verbatim (so resolved_style.background there reads as the
    parent's own value -- the object IS the parent's; render, not that
    field, is where "my own fill" must be judged for an unauthored nested
    scope; see render_nested_board's own_background in render/boards.py)."""

    _YAML = """\
title: Root
style:
  background: "rgba(0, 0, 0, 0.5)"
rows:
  - title: Child
    text: "hello"
"""

    def test_unauthored_nested_board_paints_no_second_tint(self):
        from ..._svg_render import render_board_to_svg

        svg = render_board_to_svg(self._YAML)
        # Root legitimately paints its own tint twice -- the page rect and
        # the layout's own "under items" card, both the ROOT's own value.
        # A third occurrence would be Child's render_nested_board wrapper
        # rect re-painting the SAME tint on top; that must not happen, and
        # its own rect must read "transparent" instead.
        assert svg.count('fill="rgba(0, 0, 0, 0.5)"') == 2

    def test_unauthored_nested_canvas_is_parents_for_contrast(self):
        result = compile(self._YAML)
        assert result.success, result.errors
        board = result.board
        assert board is not None
        child = board.layout.items[0].board
        assert child is not None
        assert (
            child.chart_style_context.ink_canvas == board.chart_style_context.ink_canvas
        )


class TestNestedBoardAuthoringUnrelatedKeyStillPaintsNoSecondTint:
    """A nested board authoring an unrelated key (``gap``, no ``background``)
    takes the OTHER branch of compile_board_resolved_style (board_style is
    not None) -- unlike the pure-fast-path case above, this scope always
    gets its own, non-shared resolved_style object, so this guards a
    genuinely different code path: own_patch.background must still be
    pinned transparent rather than crossing in from parent_patch via the
    ordinary scope_patch/merge_patches cascade every other field uses."""

    _YAML = """\
title: Root
style:
  background: "rgba(0, 0, 0, 0.5)"
rows:
  - title: Child
    style:
      gap: 12
    text: "hello"
"""

    def test_paints_no_second_tint(self):
        from ..._svg_render import render_board_to_svg

        svg = render_board_to_svg(self._YAML)
        assert svg.count('fill="rgba(0, 0, 0, 0.5)"') == 2

    def test_resolved_style_background_is_transparent_not_inherited(self):
        result = compile(self._YAML)
        assert result.success, result.errors
        board = result.board
        assert board is not None
        child = board.layout.items[0].board
        assert child is not None
        assert child.resolved_style.background == "transparent"


class TestNestedBoardOwnTranslucentBackgroundCompositesOnce:
    """A nested board that DOES author its own translucent background
    composites it exactly once over its parent's already-composited
    canvas -- not the theme's raw canvas underneath that, and not a
    double application of the same tint."""

    _YAML = """\
title: Root
theme: neon
style:
  background: "rgba(255, 255, 255, 0.08)"
rows:
  - title: Child
    style:
      background: "rgba(255, 255, 255, 0.08)"
    text: "hello"
"""

    def test_own_translucent_background_composites_once_over_parents_canvas(self):
        result = compile(self._YAML)
        assert result.success, result.errors
        board = result.board
        assert board is not None
        root_canvas = board.chart_style_context.ink_canvas
        # Hand-composited: rgba(255, 255, 255, 0.08) over neon's own raw canvas.
        assert root_canvas == "#292929"
        child = board.layout.items[0].board
        assert child is not None
        # One more composite of the same tint on top of the root's own.
        assert child.chart_style_context.ink_canvas == "#3a3a3a"
        assert child.chart_style_context.ink_canvas != root_canvas


class TestUnstyledGrandchildrenResolveTheirOwnParentsStyleNotASiblings:
    """Six identical styled siblings (same font + background) under one
    root, each with two unstyled grandchildren. Font (an ordinary
    cascading field) and ink canvas (contrast, composited down the tree)
    must match the grandchild's OWN parent -- guards the style-cascade
    cache key against conflating two distinct board scopes that happen to
    produce the same style. background itself is excluded from the match:
    it is non-inherited, so an unstyled grandchild's own fill is
    transparent even though its sibling parent authored one."""

    @staticmethod
    def _sibling(n: int) -> dict:
        return {
            "title": f"Sibling{n}",
            "style": {"font": {"family": "F0"}, "background": "#000000"},
            "rows": [{"text": "grandchild-a"}, {"text": "grandchild-b"}],
        }

    def test_every_grandchild_resolves_its_own_parents_font_and_canvas(self):
        board = normalize_board(
            AuthoredBoard.model_validate(
                {
                    "title": "Root",
                    "style": {"background": "#eeeeee"},
                    "cols": [self._sibling(n) for n in range(6)],
                }
            )
        )
        for item in board.layout.items:
            sibling = item.board
            assert sibling is not None
            # startswith, not equality: the cascade appends an emoji-fallback
            # family after the authored one (font.emoji default) -- an
            # unrelated theme default this test must not pin.
            assert sibling.resolved_style.font.family.startswith("F0")
            for grandchild_item in sibling.layout.items:
                grandchild = grandchild_item.board
                assert grandchild is not None
                assert grandchild.resolved_style.font.family.startswith("F0")
                assert (
                    grandchild.chart_style_context.ink_canvas
                    == sibling.chart_style_context.ink_canvas
                )


class TestChartsBackgroundCardFillNeverDarkensAcrossNestingLevels:
    """style.charts.background is a chart card-fill DEFAULT -- it cascades
    like any style.charts.* field, but never touches the board's own
    canvas. Three levels deep, each authoring only an unrelated key
    (gap), the board canvas stays exactly the root's -- no darkening per
    nesting level -- while the card-fill default keeps cascading down
    unchanged."""

    _YAML = """\
title: Root
theme: neon
style:
  charts:
    background: "rgba(255, 255, 255, 0.08)"
rows:
  - title: Level1
    style:
      gap: 12
    rows:
      - title: Level2
        style:
          gap: 12
        rows:
          - title: Level3
            style:
              gap: 12
            text: leaf
"""

    def test_no_darkening_across_three_nesting_levels(self):
        result = compile(self._YAML)
        assert result.success, result.errors
        board = result.board
        assert board is not None
        root_canvas = board.chart_style_context.ink_canvas
        root_card_fill = board.chart_style_context.background

        level1 = board.layout.items[0].board
        assert level1 is not None
        level2 = level1.layout.items[0].board
        assert level2 is not None
        level3 = level2.layout.items[0].board
        assert level3 is not None

        for level in (level1, level2, level3):
            assert level.chart_style_context.ink_canvas == root_canvas
            assert level.chart_style_context.background == root_card_fill


class TestThemeBackgroundTokenResolvesTheThemesRawCanvas:
    """``theme.background`` is a theme-self token resolved against the
    unmodified theme base, never against this board's own (possibly
    non-inherited-transparent) resolved background. A nested board that
    authors an unrelated style key must still resolve a
    theme.background-linked stroke to the theme's own canvas, not
    'transparent'."""

    _YAML = """\
title: Root
theme: stark
style:
  charts:
    marks:
      slice:
        stroke:
          color: theme.background
rows:
  - title: Child
    style:
      font:
        family: Arial
    text: hi
"""

    def test_nested_board_slice_stroke_resolves_theme_canvas_not_transparent(self):
        result = compile(self._YAML)
        assert result.success, result.errors
        board = result.board
        assert board is not None
        child = board.layout.items[0].board
        assert child is not None
        from dbt_charts.core.compile.config import get_theme_style

        expected = get_theme_style("stark").background
        assert child.chart_style_context.marks.slice.stroke.color == expected
        assert child.chart_style_context.marks.slice.stroke.color != "transparent"


class TestRepeatedDonutTitleTypographyIsPinnedAcrossUnstyledWrapperWidths:
    """The same donut chart (chart_id) placed under three unstyled `cols:`
    wrappers of different widths must keep identical title typography at
    every width -- layout_sizing.py's canonical_resolved cache pins the
    first-resolved placement's title font and every later placement of the
    same chart_id reuses it. Since compile_board_resolved_style now gives
    every board scope its own resolved_style object (no verbatim reuse --
    see normalize/dispatch.py), that pin is keyed by VALUE
    (canonical_resolved_key), not id(resolved_style): an identity key would
    treat these three equal-but-distinct unstyled scopes as different
    placements, and the narrowest would resolve its own, smaller title
    tier instead of inheriting the widest one's."""

    _YAML = """\
title: Root
queries:
  q:
    columns: [k, v]
    values:
      - [a, 1]
      - [b, 2]
      - [c, 3]
charts:
  donut1:
    query: q
    type: donut
    theta: v
    color: k
    title: Share
cols:
  - width: 600
    rows:
      - donut1
  - width: 300
    rows:
      - donut1
  - width: 100
    rows:
      - donut1
"""

    def _title_font_attrs(self, svg: str) -> list[tuple[str, str]]:
        import re

        # vl-convert's text metrics are platform-dependent: on Linux the
        # narrowest title's limit truncates "Share" to an ellipsis.
        return re.findall(
            r'<text[^>]*font-family="([^"]*)"[^>]*font-size="([^"]*)"[^>]*>'
            r"(?:Share|[^<]*…)</text>",
            svg,
        )

    def test_narrowest_placement_keeps_the_widest_placements_typography(self):
        from ..._svg_render import render_board_to_svg

        svg = render_board_to_svg(self._YAML)
        title_fonts = self._title_font_attrs(svg)
        assert len(title_fonts) == 3, title_fonts
        assert len(set(title_fonts)) == 1, title_fonts

    def test_lone_narrow_donut_would_otherwise_pick_a_smaller_tier(self):
        """Confirms the pin in the test above is load-bearing: a narrow
        donut resolved with no wider sibling picks a genuinely different
        (smaller, sans-serif) title tier on its own."""
        from ..._svg_render import render_board_to_svg

        lone_narrow = """\
title: Root
queries:
  q:
    columns: [k, v]
    values:
      - [a, 1]
      - [b, 2]
      - [c, 3]
charts:
  donut1:
    query: q
    type: donut
    theta: v
    color: k
    title: Share
cols:
  - width: 100
    rows:
      - donut1
"""
        svg = render_board_to_svg(lone_narrow)
        [(family, size)] = self._title_font_attrs(svg)
        pinned_svg = render_board_to_svg(self._YAML)
        [(pinned_family, pinned_size), *_rest] = self._title_font_attrs(pinned_svg)
        assert (family, size) != (pinned_family, pinned_size)


class TestBoardCanvasIsAlwaysOpaqueNeverTheTransparentLiteral:
    """ink_canvas -- what undercoat/halo/knockout consumers read for
    contrast (support_table_attachment.py's ``charts_style.ink_canvas``)
    -- is always a real opaque color, never the literal 'transparent', on
    both a top-level board and an unstyled nested board underneath a
    translucent root background."""

    _YAML = """\
title: Root
style:
  background: "rgba(0, 0, 0, 0.5)"
rows:
  - title: Child
    text: hello
"""

    def test_root_and_unstyled_nested_canvas_are_opaque(self):
        from dbt_charts.core.colors import parse_css_color

        result = compile(self._YAML)
        assert result.success, result.errors
        board = result.board
        assert board is not None
        child = board.layout.items[0].board
        assert child is not None

        for ctx in (board.chart_style_context, child.chart_style_context):
            canvas = ctx.ink_canvas
            assert canvas != "transparent"
            _, _, _, alpha = parse_css_color(canvas)
            assert alpha == 1.0


class TestChartsBackgroundAloneSetsChartInkCanvasNotJustCardFill:
    """style.charts.background on a ROOT board (no nesting involved) must
    feed the chart ink canvas chart-ink helpers derive label/mark contrast
    against, not just the raw card paint -- two canvases per the model:
    board canvas (own fill over parent board canvas) and chart canvas
    (charts.background over the board canvas), and chart ink reads the
    latter. Guards CRITICAL #2: ink_canvas silently reverting to the
    board's own fill (the theme default here) while the card paints a
    different color picks contrast for the wrong background."""

    _YAML = """
title: Root
style:
  charts:
    background: "#111111"
rows:
  - text: hello
"""

    def test_root_charts_background_sets_ink_canvas_and_label_contrast(self):
        from dbt_charts.core.colors import wcag_contrast

        result = compile(self._YAML)
        assert result.success, result.errors
        board = result.board
        assert board is not None
        ctx = board.chart_style_context
        assert ctx.ink_canvas == "#111111"
        for mark, ink in zip(ctx.palette, ctx.dark_companion_palette, strict=True):
            assert wcag_contrast(ink, ctx.ink_canvas) >= 4.5 - 1e-6, (mark, ink)
