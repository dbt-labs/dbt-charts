"""The tab bar: content-sized tabs on one continuous outline rule; the selected
tab takes its pane's background."""

import math
import re

from dbt_charts.core.colors import wcag_contrast
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.board.resolved import ResolvedLayoutItem
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.font_measure import get_weighted_font_measurer
from dbt_charts.core.render.layouts import render_tabs_layout

from ._svg_render import render_board_to_svg

# Tab labels are title-cased by the theme, so author them cased.
LONG_LABEL = "Quarterly Revenue Breakdown Across Every Single Market Segment Today"


def _bar(
    titles: list[str],
    *,
    active: int = 0,
    theme: str | None = None,
    tab_variable: str | None = "view",
    tab_position: str = "top",
) -> str:
    item = ResolvedLayoutItem(
        type="chart", chart=None, board=None, x=0.0, y=0.0, width=400.0, height=200.0
    )
    theme_style = get_theme_style(theme)
    layout = theme_style.layout
    tabs = layout.tabs.model_copy(update={"position": tab_position})
    style = resolve_style(
        theme_style.model_copy(
            update={"layout": layout.model_copy(update={"tabs": tabs})}
        )
    )
    svg, _ = render_tabs_layout(
        items=(item,) * len(titles),
        results=None,
        variables={tab_variable: f"t{active}"} if tab_variable else {},
        available_width=800.0,
        available_height=300.0,
        card_gap=0.0,
        tab_titles=titles,
        tab_slugs=[f"t{i}" for i in range(len(titles))],
        tab_variable=tab_variable,
        resolved_style=style,
        render_cache={},
        painted_canvas=None,
    )
    return svg


def _centers(svg: str) -> list[float]:
    tabs = re.findall(r'translate\(([\d.]+), 0\)"[^>]*>.*?<text x="([\d.]+)"', svg)
    return [float(x) + float(cx) for x, cx in tabs]


def test_tab_bar_has_no_visible_tab_fill() -> None:
    rects = re.findall(r"<rect [^>]*/>", _bar(["One", "Two"]))
    assert len(rects) == 4
    assert all('fill="transparent"' in r for r in rects)


def test_inactive_tab_link_contains_a_full_cell_hit_rect() -> None:
    svg = _bar(["One", "Two"], active=0)
    link = re.search(r"<a [^>]*>.*?</a>", svg)
    assert link is not None
    bar_height = resolve_style(get_theme_style()).layout.tabs.bar_height
    assert re.search(
        rf'translate\(96.0, 0\)".*<rect class="dbt-box-inner" x="0" y="0" '
        rf'width="96" height="{bar_height:g}" fill="transparent"/>',
        link.group(0),
    )


def test_active_path_uses_theme_border_and_continues_to_full_width() -> None:
    style = resolve_style(get_theme_style())
    border = style.layout.tabs.border
    svg = _bar(["One", "Two", "Three"], active=1)
    (path,) = re.findall(r"<path [^>]*/>", svg)
    assert f'stroke="{border.color}"' in path
    assert f'stroke-width="{border.width}"' in path
    assert 'fill="none"' in path
    assert 'stroke-linejoin="round"' in path
    d = re.search(r' d="([^"]+)"', path)
    assert d is not None
    assert d.group(1).startswith("M 0 ")
    assert d.group(1).endswith("H 800.0")


def test_neon_line_is_chrome_8_not_inactive_ink() -> None:
    neon = resolve_style(get_theme_style("neon"))
    clarity = resolve_style(get_theme_style())
    assert neon.layout.tabs.border.color != clarity.layout.tabs.border.color
    assert f'stroke="{neon.layout.tabs.border.color}"' in _bar(["A", "B"], theme="neon")


def test_widths_are_content_sized_within_min_and_max() -> None:
    style = resolve_style(get_theme_style())
    tabs = style.layout.tabs
    svg = _bar(["Hi", LONG_LABEL, "Overview Everything Today"])
    c = _centers(svg)
    first = c[0] * 2  # tab 0 spans [0, w0]; its center is w0/2
    assert first == tabs.min_width
    second = (c[1] - first) * 2
    assert second == tabs.max_width
    font = tabs.font
    assert font.family is not None
    measurer = get_weighted_font_measurer(font.family, tabs.active_weight)
    expected = math.ceil(
        measurer.measure("Overview Everything Today", font.size) + 2 * tabs.padding_x
    )
    third = (c[2] - first - second) * 2
    assert third == expected


def test_overlong_label_truncates_with_ellipsis_and_title() -> None:
    svg = _bar(["Short", LONG_LABEL])
    assert f"<title>{LONG_LABEL}</title>" in svg
    assert "…</text>" in svg
    assert f">{LONG_LABEL}</text>" not in svg


def test_fitting_label_has_no_title() -> None:
    assert "<title>" not in _bar(["Short", "Other"])


def test_every_tab_text_carries_font_family() -> None:
    svg = _bar(["One", "Two", "Three"])
    texts = re.findall(r"<text [^>]*>", svg)
    assert len(texts) == 3
    assert all("font-family=" in t for t in texts)


def test_aria_roles_and_selected_state() -> None:
    svg = _bar(["One", "Two"], active=1)
    assert 'role="tablist"' in svg
    assert svg.count('role="tab"') == 2
    assert svg.count('aria-selected="true"') == 1
    assert svg.count('aria-selected="false"') == 1
    assert svg.count("<a href=") == 1
    assert re.search(r'<a href="[^"]*"[^>]*role="tab"[^>]*aria-selected="false"', svg)


def test_active_label_weight_and_ink() -> None:
    style = resolve_style(get_theme_style())
    tabs = style.layout.tabs
    svg = _bar(["One", "Two"], active=0)
    a, b = re.findall(r"<text [^>]*>", svg)
    assert f'font-weight="{tabs.active_weight}"' in a
    assert f'font-weight="{tabs.inactive_weight}"' in b
    assert f'fill="{style.title.font.color}"' in a
    assert f'fill="{style.variables.font.color}"' in b


def test_theme_tabs_font_is_the_variables_family() -> None:
    style = resolve_style(get_theme_style())
    assert style.layout.tabs.font.family == style.variables.font.family


def _pane_svg(tab_yaml: str) -> str:
    return render_board_to_svg(f"tabs:\n  Engagement:\n{tab_yaml}")


def _label_count(svg: str, label: str) -> int:
    texts = re.findall(r"<text[^>]*>(.*?)</text>", svg, re.S)
    return sum(re.sub(r"<[^>]+>", "", t).strip() == label for t in texts)


def test_tab_title_is_drawn_once_and_pane_content_sits_under_the_bar() -> None:
    svg = _pane_svg("      text: BODYMARKER\n")
    assert _label_count(svg, "Engagement") == 1
    match = re.search(r'<text x="[\d.]+" y="([\d.]+)"[^>]*>BODYMARKER', svg)
    assert match is not None
    # No heading band above the prose: its first line sits at the top of the pane.
    assert float(match.group(1)) < 20, match.group(1)


def test_tab_label_is_drawn_once_for_nested_rows() -> None:
    nested = _pane_svg("      rows:\n        - text: BODYMARKER\n")
    assert _label_count(nested, "Engagement") == 1


def test_each_tab_carries_its_pane_path_and_a_box_to_mark() -> None:
    from xml.etree import ElementTree

    svg = render_board_to_svg("tabs:\n  One:\n    text: A\n  Two:\n    text: B\n")
    root = ElementTree.fromstring(svg)
    tabs = [el for el in root.iter() if el.get("role") == "tab"]
    assert len(tabs) == 2
    for label, tab in zip(("One", "Two"), tabs, strict=True):
        assert tab.get("data-authored-path") == f"tabs.{label}"
        assert tab.get("data-authored-kind") == "tab"
        boxes = [c.get("class") for c in tab if c.tag.endswith("rect")]
        assert "dbt-box-outer" in boxes


def _tablist_chain(svg: str) -> list[tuple[float, float]]:
    """Each ancestor ``<g>`` translate down to the tablist, outermost first."""
    from xml.etree import ElementTree

    def walk(
        el: ElementTree.Element, chain: list[tuple[float, float]]
    ) -> list[tuple[float, float]] | None:
        moved = re.fullmatch(
            r"translate\(([\d.]+), ([\d.]+)\)", el.get("transform", "")
        )
        here = [*chain, (float(moved[1]), float(moved[2]))] if moved else chain
        if el.get("role") == "tablist":
            return here
        for child in el:
            found = walk(child, here)
            if found is not None:
                return found
        return None

    chain = walk(ElementTree.fromstring(svg), [])
    assert chain is not None
    return chain


_CARD_PADDING = resolve_style(get_theme_style()).frame.card_padding
_BAR_HEIGHT = resolve_style(get_theme_style()).layout.tabs.bar_height
_TWO_TABS = "  One:\n    text: A\n  Two:\n    text: B\n"
_LEFT = "style:\n  layout:\n    tabs:\n      position: left\n"


def test_root_tabs_bar_sits_a_card_padding_below_title_and_filters() -> None:
    svg = render_board_to_svg(
        "title: T\nvariables:\n  a:\n    input: select\n    options:\n"
        "      static: [x, y]\n    default: x\ntabs:\n" + _TWO_TABS
    )
    slot, inset = _tablist_chain(svg)[-2:]
    assert inset == (0.0, _CARD_PADDING)
    # The inset is the bar's own: the slot top is still the filters' bottom.
    assert re.search(rf'<g transform="translate\(36, {slot[1]:g}\)"><rect ', svg)


def test_bare_tabs_board_insets_the_bar_like_card_ink() -> None:
    chain = _tablist_chain(render_board_to_svg("tabs:\n" + _TWO_TABS))
    assert chain[-1] == (0.0, _CARD_PADDING)


def test_tabs_nested_after_a_text_row_inset_the_bar() -> None:
    svg = render_board_to_svg(
        "rows:\n  - text: ABOVE\n  - tabs:\n"
        "      One:\n        text: A\n"
        "      Two:\n        text: B\n"
    )
    assert _tablist_chain(svg)[-1] == (0.0, _CARD_PADDING)


def test_panel_starts_below_inset_plus_bar_and_height_counts_it() -> None:
    svg = render_board_to_svg("tabs:\n" + _TWO_TABS)
    content_y = _CARD_PADDING + _BAR_HEIGHT
    assert re.search(rf'translate\(0, {content_y:g}\)"', svg)
    height = re.search(
        r'<rect x="0" y="0" width="[\d.]+" height="([\d.]+)" fill="#FFFFFF" rx', svg
    )
    assert height is not None
    assert float(height[1]) > content_y


def _rail_cells(svg: str) -> list[tuple[float, float]]:
    return [
        (float(x), float(y))
        for x, y in re.findall(
            r'role="tab"[^>]*transform="translate\(([\d.]+), ([\d.]+)\)"', svg
        )
    ]


def test_left_rail_stacks_tabs_vertically_at_one_shared_width() -> None:
    svg = _bar(["Hi", "Overview Everything Today", "Three"], tab_position="left")
    assert _rail_cells(svg) == [(0.0, 0.0), (0.0, _BAR_HEIGHT), (0.0, 2 * _BAR_HEIGHT)]
    widths = set(re.findall(r'class="dbt-box-inner" x="0" y="0" width="([\d.]+)"', svg))
    assert len(widths) == 1
    assert 'aria-orientation="vertical"' in svg
    texts = re.findall(r"<text [^>]*>", svg)
    assert all('text-anchor="start"' in t for t in texts)


def test_left_rail_outline_opens_right_onto_a_full_height_rule() -> None:
    svg = _bar(["One", "Two", "Three"], active=1, tab_position="left")
    (path,) = re.findall(r"<path [^>]*/>", svg)
    d = re.search(r' d="([^"]+)"', path)
    assert d is not None
    rule_x = d.group(1).split()[1]
    # Starts on the rule at the rail's top, wraps the active cell, ends on the rule.
    assert d.group(1).startswith(f"M {rule_x} 0 V {_BAR_HEIGHT + 0.5}")
    assert f"H {rule_x} V " in d.group(1)


def test_left_rail_pane_sits_right_of_rail_and_gutter() -> None:
    svg = render_board_to_svg(_LEFT + "tabs:\n" + _TWO_TABS)
    assert _tablist_chain(svg)[-1] == (0.0, _CARD_PADDING)
    cell = re.search(r'class="dbt-box-inner" x="0" y="0" width="([\d.]+)"', svg)
    assert cell is not None
    pane_x = float(cell[1]) + _CARD_PADDING
    assert re.search(rf'translate\({pane_x:g}, 0\)"', svg)
    assert not re.search(rf'translate\(0, {_CARD_PADDING + _BAR_HEIGHT:g}\)"', svg)


def test_left_rail_board_is_never_shorter_than_its_rail() -> None:
    items = "".join(f"  T{i}:\n    text: x\n" for i in range(8))
    svg = render_board_to_svg(_LEFT + "tabs:\n" + items)
    height = re.search(
        r'<rect x="0" y="0" width="[\d.]+" height="([\d.]+)" fill="#FFFFFF" rx', svg
    )
    assert height is not None
    assert float(height[1]) >= _CARD_PADDING + 8 * _BAR_HEIGHT


TWO_LINE_LABEL = "Quarterly Revenue Breakdown by Region"


def test_left_rail_cuts_a_long_label_on_one_line_with_a_tooltip() -> None:
    svg = _bar(["One", TWO_LINE_LABEL, "Three"], tab_position="left")
    assert "<tspan" not in svg
    assert "…</text>" in svg
    assert f"<title>{TWO_LINE_LABEL}</title>" in svg
    # Every cell is one bar high, the long label's included.
    assert _rail_cells(svg)[2] == (0.0, 2 * _BAR_HEIGHT)


def test_top_bar_still_truncates_a_long_label_on_one_line() -> None:
    svg = _bar(["One", TWO_LINE_LABEL])
    assert "<tspan" not in svg
    assert "…</text>" in svg


def test_left_rail_height_counts_one_bar_per_tab() -> None:
    items = "".join(f'  "{TWO_LINE_LABEL} {i}":\n    text: x\n' for i in range(6))
    svg = render_board_to_svg(_LEFT + "tabs:\n" + items)
    height = re.search(
        r'<rect x="0" y="0" width="[\d.]+" height="([\d.]+)" fill="#FFFFFF" rx', svg
    )
    assert height is not None
    assert float(height[1]) >= _CARD_PADDING + 6 * _BAR_HEIGHT


def test_a_cut_tab_label_collapses_whitespace_runs_as_the_engine_does() -> None:
    label = "Quarterly   Revenue    Breakdown   Across  Every   Single  Market   Segment Today"
    for position in ("top", "left"):
        svg = _bar(["One", label], tab_position=position)
        text = re.findall(r'role="tab".*?</text>', svg)[1]
        shown = re.search(r">([^<>]*…)</text>", text)
        assert shown is not None, text
        assert "  " not in shown[1]
        assert f"<title>{label}</title>" in svg


def _pane_tabs(first_style: str, *, position: str = "top") -> str:
    """A two-tab board whose first pane takes ``first_style`` and second a color."""
    return (
        (
            f"style:\n  layout:\n    tabs:\n      position: {position}\n"
            if position != "top"
            else ""
        )
        + "tabs:\n  One:\n    text: A\n"
        + first_style
        + "  Two:\n    text: B\n"
        "    style:\n      background: '#0000aa'\n"
    )


def _tab_fill(svg: str) -> str | None:
    """The fill of the path painted under the tab bar's rule, if one is drawn."""
    (paths,) = re.findall(r'<g role="tablist"[^>]*>((?:<path [^>]*/>)+)', svg)
    *fills, rule = re.findall(r"<path [^>]*/>", paths)
    assert 'fill="none"' in rule
    if not fills:
        return None
    (fill,) = fills
    assert "stroke" not in fill
    found = re.search(r'fill="([^"]+)"', fill)
    assert found is not None
    return found.group(1)


_PANE_FILL = "    style:\n      background: '#00aa00'\n"


def test_selected_tab_fills_with_its_pane_background() -> None:
    assert _tab_fill(render_board_to_svg(_pane_tabs(_PANE_FILL))) == "#00aa00"


def test_left_rail_selected_tab_fills_with_its_pane_background() -> None:
    svg = render_board_to_svg(_pane_tabs(_PANE_FILL, position="left"))
    assert _tab_fill(svg) == "#00aa00"


def test_selected_tab_of_a_pane_without_background_draws_no_fill() -> None:
    assert _tab_fill(render_board_to_svg(_pane_tabs(""))) is None


def test_selected_tab_takes_the_open_pane_background_not_a_sibling() -> None:
    svg = render_board_to_svg(
        _pane_tabs(_PANE_FILL), variables={"_tab_untitled_board": "two"}
    )
    assert _tab_fill(svg) == "#0000aa"


def test_tab_fill_follows_the_rule_around_the_tab_and_reaches_the_pane() -> None:
    svg = render_board_to_svg(_pane_tabs(_PANE_FILL))
    fill, rule = re.findall(r'<path d="([^"]+)"', svg)[:2]
    stroke = resolve_style(get_theme_style()).layout.tabs.border.width
    # The same walk as the rule, met at the bar's bottom edge, where the pane starts.
    assert fill == rule.replace(f"{_BAR_HEIGHT - stroke / 2:g}", f"{_BAR_HEIGHT}")


def _selected_label_ink(svg: str) -> str:
    found = re.search(r'aria-selected="true".*?<text [^>]*? fill="([^"]+)"', svg)
    assert found is not None
    return found.group(1)


def _account_tabs(pane_style: str, theme: str = "") -> str:
    return (
        theme + "tabs:\n  Overview:\n    text: On track.\n"
        "    style:\n" + pane_style + "  Renewals:\n    text: Forty.\n"
    )


def test_selected_label_flips_to_read_on_its_fill_in_both_directions() -> None:
    dark_pane = render_board_to_svg(_account_tabs("      background: '#17324d'\n"))
    assert _selected_label_ink(dark_pane) == "#ffffff"
    light_pane = render_board_to_svg(
        _account_tabs("      background: '#dbeafe'\n", theme="theme: neon\n")
    )
    assert _selected_label_ink(light_pane) == "#000000"
    for svg, fill in ((dark_pane, "#17324d"), (light_pane, "#dbeafe")):
        assert wcag_contrast(_selected_label_ink(svg), fill) >= 4.5


def test_selected_label_keeps_the_title_ink_where_it_reads() -> None:
    title_ink = resolve_style(get_theme_style()).title.font.color
    unfilled = render_board_to_svg(_pane_tabs(""))
    assert _selected_label_ink(unfilled) == title_ink
    pale = render_board_to_svg(_account_tabs("      background: '#dbeafe'\n"))
    assert _selected_label_ink(pale) == title_ink


def test_selected_label_takes_the_pane_title_ink_where_it_reads() -> None:
    title = "      title:\n        font:\n          color: '{}'\n"
    navy = render_board_to_svg(
        _account_tabs("      background: '#dbeafe'\n" + title.format("#17324d"))
    )
    assert _selected_label_ink(navy) == "#17324d"
    pale = render_board_to_svg(
        _account_tabs("      background: '#dbeafe'\n" + title.format("#e0f0ff"))
    )
    assert _selected_label_ink(pale) == "#000000"
    bare = render_board_to_svg(_account_tabs(title.format("#17324d")))
    assert (
        _selected_label_ink(bare) == resolve_style(get_theme_style()).title.font.color
    )


def test_selected_label_reads_on_a_translucent_fill_over_the_board() -> None:
    # Navy at 90% over the white board is still dark; at 10% it is still light.
    title_ink = resolve_style(get_theme_style()).title.font.color
    for alpha, ink in ((0.9, "#ffffff"), (0.1, title_ink)):
        svg = render_board_to_svg(
            _account_tabs(f"      background: 'rgba(23, 50, 77, {alpha})'\n")
        )
        assert _selected_label_ink(svg) == ink


def test_tab_fill_names_the_ink_its_label_takes_on_the_bare_bar() -> None:
    svg = render_board_to_svg(_account_tabs("      background: '#17324d'\n"))
    title_ink = resolve_style(get_theme_style()).title.font.color
    assert f'fill="#17324d" data-bar-ink="{title_ink}"/>' in svg
