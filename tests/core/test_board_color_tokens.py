"""Tests for accent/secondary-font board-to-board cascade on Style/ResolvedStyle.

Accent is a semantic color token defined on Style, and the
secondary text ink is the named font ``fonts.secondary``.
They live at root level (1d) and cascade to sub-fields within a board
(_apply_token_cascade). Board-to-board cascade via compile_board_resolved_style()
(1c) is per-field, driven by each Style field's own Merge marker (see
compile/models/markers.py and style/theme/style.py): most fields a parent
board explicitly authors — fonts, accent, font, or anything else without a
NotInherited marker — reach a nested board, falling back to that
nested board's own theme for anything no ancestor authored. A field marked
NotInherited (a per-board structural or root-only concern, not
thematic identity) never crosses a board boundary; the published
AuthoredBoard.style description enumerates the current set, and
test_authored_board_style_description_names_every_not_inherited_field below
keeps that enumeration honest.

`background` is the one exception with no marker at all: CSS
`background-color` is not inherited, so a nested board's unset background is
pinned to transparent at compile_board_resolved_style's own call site
(_pin_unauthored_nested_background in normalize/dispatch.py) rather than
crossing in from an ancestor's patch like every field above.
"""

import pytest

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


def test_compiled_style_accepts_accent():
    s = get_theme_style().model_copy(update={"accent": "#ff0000"})
    assert s.accent == "#ff0000"


# ---------------------------------------------------------------------------
# resolve_style — passthrough into ResolvedStyle
# ---------------------------------------------------------------------------


def test_resolve_style_accent_passthrough():
    resolved = resolve_style(get_theme_style().model_copy(update={"accent": "#3b82f6"}))
    assert resolved.accent == "#3b82f6"


def _secondary(color: str) -> dict:
    return {"fonts": {"secondary": {"color": color}}}


def test_resolve_style_secondary_color_via_patch():
    patch = StylePatch.model_validate(_secondary("#aabbcc"))
    resolved = resolve_style(get_theme_style(), patch)
    assert resolved.secondary_color == "#aabbcc"


def test_resolve_style_accent_via_patch():
    patch = StylePatch.model_validate({"accent": "#ff0000"})
    resolved = resolve_style(get_theme_style(), patch)
    assert resolved.accent == "#ff0000"


def test_resolve_style_returns_resolved_style_type_with_tokens():
    resolved = resolve_style(get_theme_style().model_copy(update={"accent": "#3b82f6"}))
    assert isinstance(resolved, ResolvedStyle)
    assert resolved.accent == "#3b82f6"


# ---------------------------------------------------------------------------
# Compile cascade — token values reach spark sub-fields via _apply_token_cascade
# NOTE: these verify the resolved compile output, NOT SVG renderer output.
# The renderer reads bar.background via resolved_style.spark_bar.bar.background.
# ---------------------------------------------------------------------------


def test_secondary_font_does_not_cascade_to_spark_bar_track():
    """The secondary font is text ink; the track fill stays theme-pinned."""
    base = get_theme_style()
    patch = StylePatch.model_validate(_secondary("#ff00ff"))
    resolved = resolve_style(base, patch)
    assert resolved.secondary_color == "#ff00ff"
    ctx = resolve_chart_style_context(base, patch)
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


def test_secondary_color_cascades_to_child_resolved_style():
    child_rs = _compile_and_get_child_resolved(parent_style=_secondary("#aabbcc"))
    assert child_rs.secondary_color == "#aabbcc"


def test_accent_cascades_to_child_resolved_style():
    child_rs = _compile_and_get_child_resolved(parent_style={"accent": "#3b82f6"})
    assert child_rs.accent == "#3b82f6"


def test_child_secondary_color_overrides_parent():
    child_rs = _compile_and_get_child_resolved(
        parent_style=_secondary("#aabbcc"),
        child_style=_secondary("#cccccc"),
    )
    assert child_rs.secondary_color == "#cccccc"


def test_child_accent_overrides_parent():
    child_rs = _compile_and_get_child_resolved(
        parent_style={"accent": "#3b82f6"},
        child_style={"accent": "#ff0000"},
    )
    assert child_rs.accent == "#ff0000"


def test_background_does_not_cascade_to_child_with_unrelated_style_patch():
    """Unlike every other style field, background does NOT cascade from an
    ancestor's authored style -- CSS background-color is not inherited. A
    nested board authoring an unrelated key still gets its own default
    (transparent), never the parent's color threaded in."""
    child_rs = _compile_and_get_child_resolved(
        parent_style={"background": "#ff0000"},
        child_style={"frame": {"card_padding": 20}},
    )
    assert child_rs.background == "transparent"


def test_secondary_color_cascades_through_two_levels():
    """grandparent secondary font propagates all the way to grandchild."""
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard
    from dbt_charts.core.compile.normalize.dispatch import normalize_board

    grandparent = AuthoredBoard.model_validate(
        {
            "title": "grandparent",
            "style": _secondary("#aabbcc"),
            "rows": [{"title": "child", "rows": [{"title": "grandchild"}]}],
        }
    )
    compiled = normalize_board(grandparent)
    child_board = compiled.layout.items[0].board
    assert child_board is not None
    grandchild_board = child_board.layout.items[0].board
    assert grandchild_board is not None
    assert grandchild_board.resolved_style.secondary_color == "#aabbcc"


# ---------------------------------------------------------------------------
# Ancestor-authored fields beyond secondary font/accent
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


def test_margin_padding_do_not_cascade_but_gap_does_to_styled_child():
    """margin/padding are per-board (NotInherited); gap is part of the
    inheriting board spacing system, like frame and layout."""
    child_rs = _compile_and_get_child_resolved(
        parent_style={
            "gap": 60,
            "margin": {"left": 40},
            "padding": {"top": 30},
            "font": {"color": "#a1a1a1"},
        },
        child_style={"frame": {"card_padding": 20}},
    )
    assert child_rs.gap == 60
    assert child_rs.margin is None
    assert child_rs.padding is None
    assert child_rs.font.color == "#a1a1a1"


def test_frame_cascades_to_styled_child():
    theme_default = resolve_style(get_theme_style()).frame.card_padding
    child_rs = _compile_and_get_child_resolved(
        parent_style={"frame": {"card_padding": theme_default + 100}},
        child_style={"background": "#eeeeee"},
    )
    assert child_rs.frame.card_padding == theme_default + 100


def test_layout_cascades_to_styled_child():
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
                "tabs": {"bar_height": theme_default.tabs.bar_height + 100},
                "details": {
                    "summary_height": theme_default.details.summary_height + 100
                },
            }
        },
        child_style={"background": "#eeeeee"},
    )
    assert child_rs.layout.rows.gap == theme_default.rows.gap + 100
    assert child_rs.layout.cols.gap == theme_default.cols.gap + 100
    assert child_rs.layout.grid.gap == theme_default.grid.gap + 100
    assert child_rs.layout.grid.columns == theme_default.grid.columns + 10
    assert child_rs.layout.tabs.bar_height == theme_default.tabs.bar_height + 100
    assert (
        child_rs.layout.details.summary_height
        == theme_default.details.summary_height + 100
    )


def test_child_layout_field_merges_over_ancestor_layout():
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
        child_rs.layout.details.summary_height
        == theme_default.details.summary_height + 100
    )


def test_footer_timestamp_do_not_cascade_to_styled_child():
    """footer/timestamp are root-board-only chrome: a nested board has no
    footer or timestamp line of its own, so Style.footer/timestamp carry
    NotInherited."""
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


def test_authored_board_style_description_names_every_not_inherited_field() -> None:
    """The published style: field description enumerates every Style field
    marked NotInherited -- catches the next marker added without updating the
    shipped docs."""
    import re

    from dbt_charts.core.compile.models.board.authored import AuthoredBoard
    from dbt_charts.core.compile.models.markers import NotInherited
    from dbt_charts.core.compile.models.style.theme.style import Style

    marked = {
        name
        for name, field in Style.model_fields.items()
        if any(isinstance(m, NotInherited) for m in field.metadata)
    }

    description = AuthoredBoard.model_fields["style"].description
    assert description is not None
    named_in_description: set[str] = set()
    for match in re.findall(r"fields \(([^)]+)\)", description):
        named_in_description.update(name.strip() for name in match.split(","))

    assert named_in_description == marked


_NOT_INHERITED_CASES = {
    "footer": ({"text": "parent footer"}, lambda rs: rs.footer.text),
    "timestamp": ({"format": "%Y"}, lambda rs: rs.timestamp.format),
    "padding": ({"top": 30}, lambda rs: rs.padding),
    "margin": ({"left": 40}, lambda rs: rs.margin),
}


@pytest.mark.parametrize("field", list(_NOT_INHERITED_CASES))
def test_styled_nested_board_does_not_inherit_not_inherited_fields(field: str) -> None:
    authored, read = _NOT_INHERITED_CASES[field]
    theme_default = read(resolve_style(get_theme_style()))
    child_rs = _compile_and_get_child_resolved(
        parent_style={field: authored}, child_style=_secondary("#888888")
    )
    assert read(child_rs) == theme_default


def test_styled_nested_board_inherits_named_fonts() -> None:
    child_rs = _compile_and_get_child_resolved(
        parent_style={
            "fonts": {"mine": {"color": "#a1a1a1"}},
            "title": {"font": "mine"},
        },
        child_style=_secondary("#888888"),
    )
    assert child_rs.title.font.color == "#a1a1a1"


def test_unstyled_nested_board_resolves_like_a_styled_one() -> None:
    theme_default = resolve_style(get_theme_style())
    parent_style = {
        "gap": 33,
        "margin": {"left": 40},
        "padding": {"top": 30},
        "frame": {"card_padding": theme_default.frame.card_padding + 100},
        "layout": {"rows": {"gap": theme_default.layout.rows.gap + 100}},
        "footer": {"text": "parent footer"},
        "timestamp": {"format": "%Y"},
        "font": {"color": "#a1a1a1"},
    }
    unstyled = _compile_and_get_child_resolved(parent_style=parent_style)
    styled = _compile_and_get_child_resolved(
        parent_style=parent_style, child_style=_secondary("#888888")
    )
    assert unstyled.gap == styled.gap == 33
    assert unstyled.frame.card_padding == theme_default.frame.card_padding + 100
    assert unstyled.layout.rows.gap == theme_default.layout.rows.gap + 100
    assert unstyled.margin is None
    assert unstyled.padding is None
    assert unstyled.footer == theme_default.footer
    assert unstyled.timestamp == theme_default.timestamp
    assert unstyled.font.color == "#a1a1a1"
    for field in ("gap", "margin", "padding", "frame", "layout", "footer", "timestamp"):
        assert getattr(unstyled, field) == getattr(styled, field)


def test_nested_board_does_not_inherit_tags() -> None:
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard
    from dbt_charts.core.compile.normalize.dispatch import normalize_board

    board = AuthoredBoard.model_validate(
        {"title": "parent", "tags": ["finance"], "rows": [{"title": "child"}]}
    )
    child_board = normalize_board(board).layout.items[0].board
    assert child_board is not None
    assert not child_board.tags
