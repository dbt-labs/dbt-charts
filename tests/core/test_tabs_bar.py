"""The tab bar: content-sized tabs on one continuous outline rule, no fills."""

import math
import re

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.board.resolved import ResolvedLayoutItem
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
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
    style: ResolvedStyle | None = None,
    tab_variable: str | None = "view",
) -> str:
    item = ResolvedLayoutItem(
        type="chart", chart=None, board=None, x=0.0, y=0.0, width=400.0, height=200.0
    )
    svg, _ = render_tabs_layout(
        items=(item,) * len(titles),
        executor=None,
        variables={},
        available_width=800.0,
        available_height=300.0,
        tab_titles=titles,
        tab_slugs=[f"t{i}" for i in range(len(titles))],
        tab_variable=tab_variable,
        active_tab=active,
        resolved_style=style or resolve_style(get_theme_style(theme)),
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
    return render_board_to_svg(f"tabs:\n  items:\n    - title: Engagement\n{tab_yaml}")


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


def test_tab_title_is_drawn_once_for_nested_rows_and_empty_tabs() -> None:
    nested = _pane_svg("      rows:\n        - text: BODYMARKER\n")
    assert _label_count(nested, "Engagement") == 1
    assert _label_count(_pane_svg(""), "Engagement") == 1


def test_each_tab_is_its_title_leaf_with_a_box_to_mark() -> None:
    from xml.etree import ElementTree

    svg = render_board_to_svg(
        "tabs:\n  items:\n    - title: One\n      text: A\n    - title: Two\n      text: B\n"
    )
    root = ElementTree.fromstring(svg)
    tabs = [el for el in root.iter() if el.get("role") == "tab"]
    assert len(tabs) == 2
    for idx, tab in enumerate(tabs):
        path = f"tabs.items.{idx}.title"
        assert tab.get("data-authored-path") == path
        assert tab.get("data-authored-kind") == "title"
        assert svg.count(f'data-authored-path="{path}"') == 1
        boxes = [c.get("class") for c in tab if c.tag.endswith("rect")]
        assert "dbt-box-outer" in boxes
