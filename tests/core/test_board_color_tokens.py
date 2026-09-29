"""Tests for accent/muted board-to-board cascade on Style/ResolvedStyle.

ADR-003/ADR-009: accent and muted are semantic color tokens defined on Style.
They live at root level (1d) and cascade to sub-fields within a board
(_apply_token_cascade). Board-to-board cascade via compile_board_resolved_style()
(1c) is per-field, driven by each Style field's own Merge marker (see
compile/models/markers.py and style/theme/style.py): most fields a parent
board explicitly authors — muted, accent, background, font, or anything
else without a nested=Strategy.CHILD marker — reach a nested board, falling
back to that nested board's own theme for anything no ancestor authored.
A field marked nested=Strategy.CHILD (a per-board structural or root-only
concern, not thematic identity) never crosses a board boundary; the
published AuthoredBoard.style description enumerates the current set, and
test_authored_board_style_description_names_every_child_marked_field below
keeps that enumeration honest.
"""

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

# ---------------------------------------------------------------------------
# Style — field presence and defaults
# ---------------------------------------------------------------------------


def test_compiled_style_accepts_muted():
    s = get_theme_style().model_copy(update={"muted": "#888888"})
    assert s.muted == "#888888"


def test_compiled_style_accepts_accent():
    s = get_theme_style().model_copy(update={"accent": "#ff0000"})
    assert s.accent == "#ff0000"


# ---------------------------------------------------------------------------
# resolve_style — passthrough into ResolvedStyle
# ---------------------------------------------------------------------------


def test_resolve_style_muted_passthrough():
    resolved = resolve_style(get_theme_style().model_copy(update={"muted": "#888888"}))
    assert resolved.muted == "#888888"


def test_resolve_style_accent_passthrough():
    resolved = resolve_style(get_theme_style().model_copy(update={"accent": "#3b82f6"}))
    assert resolved.accent == "#3b82f6"


def test_resolve_style_muted_via_patch():
    patch = StylePatch.model_validate({"muted": "#aabbcc"})
    resolved = resolve_style(get_theme_style(), patch)
    assert resolved.muted == "#aabbcc"


def test_resolve_style_accent_via_patch():
    patch = StylePatch.model_validate({"accent": "#ff0000"})
    resolved = resolve_style(get_theme_style(), patch)
    assert resolved.accent == "#ff0000"


def test_resolve_style_returns_resolved_style_type_with_tokens():
    resolved = resolve_style(
        get_theme_style().model_copy(update={"muted": "#888888", "accent": "#3b82f6"})
    )
    assert isinstance(resolved, ResolvedStyle)
    assert resolved.muted == "#888888"
    assert resolved.accent == "#3b82f6"


# ---------------------------------------------------------------------------
# Compile cascade — token values reach spark sub-fields via _apply_token_cascade
# NOTE: these verify the resolved compile output, NOT SVG renderer output.
# The renderer reads bar.background via resolved_style.spark_bar.bar.background.
# ---------------------------------------------------------------------------


def test_muted_does_not_cascade_to_spark_bar_track():
    """muted is the secondary-text token; the track fill stays theme-pinned."""
    base = get_theme_style()
    patched = base.model_copy(update={"muted": "#ff00ff"})
    resolved = resolve_style(patched)
    assert resolved.muted == "#ff00ff"
    ctx = resolve_chart_style_context(patched)
    assert ctx.spark_bar.bar.background == base.charts.spark_bar.bar.background


def test_single_series_palette_cascades_to_resolved_spark_bar_color():
    """style.charts.single_series_palette[0] reaches resolved spark_bar.bar.color.

    Spark bars (table inline-bars, spark_bar charts) are single-series
    surfaces and pull from single_series_palette — not from the theme's
    UI-affordance accent.
    """
    base = get_theme_style()
    _cat = base.charts.color.categorical
    assert _cat is not None
    updated_cat = _cat.model_copy(update={"single_series_palette": ["#abcdef"]})
    updated_color = base.charts.color.model_copy(update={"categorical": updated_cat})
    updated_charts = base.charts.model_copy(update={"color": updated_color})
    seed = base.model_copy(update={"charts": updated_charts})
    ctx = resolve_chart_style_context(seed)
    assert ctx.spark_bar.bar.color == "#abcdef"


# ---------------------------------------------------------------------------
# AuthoredBoard-to-board cascade via compile_board_resolved_style (1c contribution)
# ---------------------------------------------------------------------------


def _compile_and_get_child_resolved(
    parent_style: dict, child_style: dict | None = None
):
    """Compile a parent->child board tree and return the child's resolved_style."""
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard
    from dbt_charts.core.compile.normalize.dispatch import normalize_board

    child: dict = {"title": "child"}
    if child_style is not None:
        child["style"] = child_style

    board = AuthoredBoard.model_validate(
        {"title": "parent", "style": parent_style, "rows": [child]}
    )
    compiled = normalize_board(board)
    child_board = compiled.layout.items[0].board
    assert child_board is not None
    return child_board.resolved_style


def test_muted_cascades_to_child_resolved_style():
    child_rs = _compile_and_get_child_resolved(parent_style={"muted": "#aabbcc"})
    assert child_rs.muted == "#aabbcc"


def test_accent_cascades_to_child_resolved_style():
    child_rs = _compile_and_get_child_resolved(parent_style={"accent": "#3b82f6"})
    assert child_rs.accent == "#3b82f6"


def test_child_muted_overrides_parent():
    child_rs = _compile_and_get_child_resolved(
        parent_style={"muted": "#aabbcc"},
        child_style={"muted": "#cccccc"},
    )
    assert child_rs.muted == "#cccccc"


def test_child_accent_overrides_parent():
    child_rs = _compile_and_get_child_resolved(
        parent_style={"accent": "#3b82f6"},
        child_style={"accent": "#ff0000"},
    )
    assert child_rs.accent == "#ff0000"


def test_background_cascades_to_child_with_unrelated_style_patch():
    """background is an ordinary style field: it cascades from an ancestor's
    authored style the same way any other field does."""
    child_rs = _compile_and_get_child_resolved(
        parent_style={"background": "#ff0000"},
        child_style={"frame": {"card_padding": 20}},
    )
    assert child_rs.background == "#ff0000"


def test_muted_cascades_through_two_levels():
    """grandparent muted propagates all the way to grandchild."""
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard
    from dbt_charts.core.compile.normalize.dispatch import normalize_board

    grandparent = AuthoredBoard.model_validate(
        {
            "title": "grandparent",
            "style": {"muted": "#aabbcc"},
            "rows": [{"title": "child", "rows": [{"title": "grandchild"}]}],
        }
    )
    compiled = normalize_board(grandparent)
    child_board = compiled.layout.items[0].board
    assert child_board is not None
    grandchild_board = child_board.layout.items[0].board
    assert grandchild_board is not None
    assert grandchild_board.resolved_style.muted == "#aabbcc"


# ---------------------------------------------------------------------------
# Ancestor-authored fields beyond muted/accent
# ---------------------------------------------------------------------------


def test_font_color_cascades_to_child_with_unrelated_style_patch():
    """An ancestor-authored style.font.color must reach a nested board even when
    that nested board authors its own, wholly unrelated style patch — a title+text
    combo item styled with padding is the common real-world shape of this."""
    child_rs = _compile_and_get_child_resolved(
        parent_style={"font": {"color": "#a1a1a1"}},
        child_style={"frame": {"card_padding": 20}},
    )
    assert child_rs.font.color == "#a1a1a1"


def test_title_font_color_cascades_to_child_with_unrelated_style_patch():
    child_rs = _compile_and_get_child_resolved(
        parent_style={"title": {"font": {"color": "#b2b2b2"}}},
        child_style={"frame": {"card_padding": 20}},
    )
    assert child_rs.title.font.color == "#b2b2b2"


def test_child_own_theme_keeps_unauthored_field_from_own_theme_not_parent():
    """A nested board on its own theme: must not inherit a color the parent's
    theme merely defaulted (never authored) — only what the parent explicitly
    wrote should cross a theme boundary.

    `border.color` has no InheritSlot fallback to `font.color`, so it is a clean
    probe for an unauthored theme default leaking across a theme boundary.
    """
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard
    from dbt_charts.core.compile.normalize.dispatch import normalize_board

    board = AuthoredBoard.model_validate(
        {
            "title": "parent",
            "style": {"font": {"color": "#a1a1a1"}},
            "rows": [
                {
                    "title": "child",
                    "theme": "neon",
                    "style": {"frame": {"card_padding": 20}},
                }
            ],
        }
    )
    compiled = normalize_board(board)
    child_board = compiled.layout.items[0].board
    assert child_board is not None
    child_rs = child_board.resolved_style

    assert child_rs.font.color == "#a1a1a1"

    neon_default = resolve_style(get_theme_style("neon"))
    clarity_default = resolve_style(get_theme_style())
    assert neon_default.border.color != clarity_default.border.color
    assert child_rs.border.color == neon_default.border.color


def test_ancestor_font_color_reaches_childs_title_ink_across_a_theme_switch():
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard
    from dbt_charts.core.compile.normalize.dispatch import normalize_board

    board = AuthoredBoard.model_validate(
        {
            "title": "parent",
            "style": {"font": {"color": "#a1a1a1"}},
            "rows": [
                {
                    "title": "child",
                    "theme": "neon",
                    "style": {"frame": {"card_padding": 20}},
                }
            ],
        }
    )
    compiled = normalize_board(board)
    child_board = compiled.layout.items[0].board
    assert child_board is not None
    child_rs = child_board.resolved_style

    assert child_rs.font.color == "#a1a1a1"
    assert child_rs.title.font.color == "#a1a1a1"


def test_gap_margin_padding_do_not_cascade_to_styled_child():
    """gap/margin/padding are per-board authoring, never theme-populated
    (Style.gap/margin/padding carry Merge(nested=Strategy.CHILD)) — a nested
    board authoring its own unrelated style patch must not inherit an
    ancestor's spacing, even though it does inherit the ancestor's color."""
    child_rs = _compile_and_get_child_resolved(
        parent_style={
            "gap": 60,
            "margin": {"left": 40},
            "padding": {"top": 30},
            "font": {"color": "#a1a1a1"},
        },
        child_style={"frame": {"card_padding": 20}},
    )
    assert child_rs.gap is None
    assert child_rs.margin is None
    assert child_rs.padding is None
    assert child_rs.font.color == "#a1a1a1"


def test_frame_does_not_cascade_to_styled_child():
    """FrameStyle's own docstring says it must not cascade to child boards —
    Style.frame carries Merge(nested=Strategy.CHILD) for the same reason as
    gap/margin/padding: each board's structural frame is its own."""
    theme_default = resolve_style(get_theme_style()).frame.card_padding
    child_rs = _compile_and_get_child_resolved(
        parent_style={"frame": {"card_padding": theme_default + 100}},
        child_style={"background": "#eeeeee"},
    )
    assert child_rs.frame.card_padding == theme_default


def test_layout_rows_cols_grid_gap_do_not_cascade_to_styled_child():
    """Row/col/grid spacing and grid column count are each board's own
    arrangement, not thematic identity, even though every theme happens to
    populate them via _base.yaml's structural floor."""
    theme_default = resolve_style(get_theme_style()).layout
    child_rs = _compile_and_get_child_resolved(
        parent_style={
            "layout": {
                "rows": {"gap": theme_default.rows.gap + 100},
                "cols": {"gap": theme_default.cols.gap + 100},
                "grid": {
                    "gap": theme_default.grid.gap + 100,
                    "columns": theme_default.grid.columns + 10,
                },
            }
        },
        child_style={"background": "#eeeeee"},
    )
    assert child_rs.layout.rows.gap == theme_default.rows.gap
    assert child_rs.layout.cols.gap == theme_default.cols.gap
    assert child_rs.layout.grid.gap == theme_default.grid.gap
    assert child_rs.layout.grid.columns == theme_default.grid.columns


def test_layout_tabs_bar_height_does_not_cascade_to_styled_child():
    """Style.layout carries a single container-level Merge(nested=CHILD): a
    nested board's own tab bar and details-accordion styling resolve against
    its own theme too, not an ancestor's, same as rows/cols/grid gap."""
    theme_default = resolve_style(get_theme_style()).layout.tabs.bar_height
    child_rs = _compile_and_get_child_resolved(
        parent_style={"layout": {"tabs": {"bar_height": theme_default + 100}}},
        child_style={"background": "#eeeeee"},
    )
    assert child_rs.layout.tabs.bar_height == theme_default


def test_layout_details_summary_height_does_not_cascade_to_styled_child():
    """Same whole-container Merge(nested=CHILD) on Style.layout, exercised
    for details (the other layout.* widget) rather than tabs."""
    theme_default = resolve_style(get_theme_style()).layout.details.summary_height
    child_rs = _compile_and_get_child_resolved(
        parent_style={"layout": {"details": {"summary_height": theme_default + 100}}},
        child_style={"background": "#eeeeee"},
    )
    assert child_rs.layout.details.summary_height == theme_default


def test_layout_field_authored_by_child_does_not_pull_in_ancestor_layout_field():
    """A nested board authoring its own layout.cols.gap still gets CHILD's
    all-or-nothing behavior for layout as a whole: its own cols.gap wins,
    and an ancestor's layout.details.summary_height resolves from its own
    theme, not the ancestor's -- merge_patches' CHILD strategy takes the
    whole field from the nested board's own patch, it does not merge
    sibling sub-fields from the ancestor in underneath it.

    Uses details/cols rather than rows/cols: both are LAYOUT_FIELDS, and
    merge_patches' cross-axis-layout-clear rule (unrelated to the CHILD
    marker under test) would zero an ancestor's rows whenever a child
    authors cols regardless of any Merge marker, confounding the result."""
    theme_default = resolve_style(get_theme_style()).layout
    child_rs = _compile_and_get_child_resolved(
        parent_style={
            "layout": {
                "details": {
                    "summary_height": theme_default.details.summary_height + 100
                }
            }
        },
        child_style={"layout": {"cols": {"gap": theme_default.cols.gap + 50}}},
    )
    assert child_rs.layout.cols.gap == theme_default.cols.gap + 50
    assert (
        child_rs.layout.details.summary_height == theme_default.details.summary_height
    )


def test_footer_timestamp_do_not_cascade_to_styled_child():
    """footer/timestamp are root-board-only chrome: a nested board has no
    footer or timestamp line of its own, so Style.footer/timestamp carry
    Merge(nested=Strategy.CHILD) like layout/frame."""
    theme_default = resolve_style(get_theme_style())
    child_rs = _compile_and_get_child_resolved(
        parent_style={
            "footer": {"text": "custom footer text"},
            "timestamp": {"format": "%Y"},
        },
        child_style={"background": "#eeeeee"},
    )
    assert child_rs.footer.text == theme_default.footer.text
    assert child_rs.timestamp.format == theme_default.timestamp.format


def test_authored_board_style_description_names_every_child_marked_field():
    """The published style: field description enumerates every Style field
    that carries Merge(nested=Strategy.CHILD) -- catches the next marker
    added without updating the shipped docs."""
    import re

    from dbt_charts.core.compile.merge import Strategy, merge_marker
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard
    from dbt_charts.core.compile.models.style.theme.style import Style

    child_marked = {
        name
        for name, field in Style.model_fields.items()
        if (marker := merge_marker(field)) is not None
        and marker.nested == Strategy.CHILD
    }

    description = AuthoredBoard.model_fields["style"].description
    assert description is not None
    named_in_description: set[str] = set()
    for match in re.findall(r"fields \(([^)]+)\)", description):
        named_in_description.update(name.strip() for name in match.split(","))

    assert named_in_description == child_marked
