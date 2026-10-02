"""cartoonize: a finished board SVG becomes a recognizable-but-unreadable thumbnail."""

import re
from xml.etree import ElementTree as ET

import pytest

from dbt_charts.core.font_measure import (
    get_font_measurer,
    get_weighted_font_measurer,
)
from dbt_charts.core.fonts import DBT_SANS_TABULAR_FONT_FAMILY
from dbt_charts.core.render.errors import RenderError
from dbt_charts.core.render.thumbnail import cartoonize

NS = "{http://www.w3.org/2000/svg}"
FONT = "Inter Variable, Noto Emoji, Inter, system-ui, sans-serif"
# White page: ink is black, so the placeholder is 20% black over white.
GRAY = "#cccccc"


# Output coordinates are rounded to hundredths.
def near(value: float) -> object:
    return pytest.approx(value, abs=0.02)


def svg(body: str, bg: str = "#FFFFFF", root_extra: str = "") -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200" '
        f'viewBox="0 0 400 200" data-dbt-page-background="{bg}" {root_extra}>'
        f"{body}</svg>"
    )


def parse(out: str) -> ET.Element:
    return ET.fromstring(out)


def rects(root: ET.Element) -> list[ET.Element]:
    return list(root.iter(f"{NS}rect"))


def num(el: ET.Element, attr: str) -> float:
    return float(el.attrib[attr])


def text_run(x: float, y: float, s: str, **attrs: str) -> str:
    a = {"font-size": "11", "font-family": FONT, "fill": "#222222", **attrs}
    attr_str = " ".join(f'{k}="{v}"' for k, v in a.items())
    return f'<text x="{x}" y="{y}" {attr_str}>{s}</text>'


def width_of(s: str, size: float = 11, weight: int = 400) -> float:
    return get_weighted_font_measurer(FONT, weight).measure(s, size)


def line_box(size: float = 11) -> tuple[float, float]:
    m = get_weighted_font_measurer(FONT, 400)
    return m.ascent_em * size, m.descent_em * size


def test_text_becomes_rounded_rect_covering_its_box() -> None:
    root = parse(cartoonize(svg(text_run(10, 30, "Hello world"))))
    assert not list(root.iter(f"{NS}text"))
    (r,) = [r for r in rects(root) if r.get("rx")]
    asc, desc = line_box()
    h = 0.48 * (asc + desc)
    assert num(r, "x") == near(10)
    assert num(r, "width") == near(width_of("Hello world"))
    assert num(r, "height") == near(h)
    assert num(r, "y") + h / 2 == near(30 - (asc - desc) / 2)
    assert num(r, "rx") == near(h / 2)
    assert r.get("fill") == GRAY
    assert r.get("fill-opacity") is None


@pytest.mark.parametrize("anchor", ["start", "middle", "end"])
def test_text_anchor_places_the_rect(anchor: str) -> None:
    w = width_of("Anchored")
    root = parse(
        cartoonize(svg(text_run(100, 20, "Anchored", **{"text-anchor": anchor})))
    )
    (r,) = [r for r in rects(root) if r.get("rx")]
    expected = {"start": 100, "middle": 100 - w / 2, "end": 100 - w}[anchor]
    assert num(r, "x") == near(expected)


def test_translate_only_vega_text_keeps_its_transform() -> None:
    body = (
        '<text text-anchor="middle" transform="translate(50,20)" '
        f'font-family="{FONT}" font-size="11px" font-weight="400" fill="#626366">'
        "Label</text>"
    )
    root = parse(cartoonize(svg(f"<g>{body}</g>")))
    (r,) = [r for r in rects(root) if r.get("rx")]
    assert r.get("transform") == "translate(50,20)"
    assert num(r, "x") == near(-width_of("Label") / 2)


def test_large_text_gets_the_taller_rect() -> None:
    root = parse(cartoonize(svg(text_run(0, 40, "Big", **{"font-size": "24"}))))
    (r,) = [r for r in rects(root) if r.get("rx")]
    asc, desc = line_box(24)
    assert num(r, "height") == near(0.6 * (asc + desc))


def test_font_size_resolves_from_class_rule_and_ancestors() -> None:
    body = (
        "<style>.big { font-size: 24px; }</style>"
        f'<g font-family="{FONT}"><text class="big" x="0" y="40">Hi</text></g>'
    )
    root = parse(cartoonize(svg(body)))
    (r,) = [r for r in rects(root) if r.get("rx")]
    asc, desc = line_box(24)
    assert num(r, "height") == near(0.6 * (asc + desc))
    assert num(r, "width") == near(width_of("Hi", 24))


def test_tspan_chunks_union_into_one_box_per_line() -> None:
    body = (
        f'<text y="30" font-size="11" font-family="{FONT}" fill="#222">'
        '<tspan x="50" text-anchor="end">$</tspan>'
        '<tspan x="90" text-anchor="end">1,234</tspan>'
        '<tspan x="95" text-anchor="start">K</tspan></text>'
    )
    root = parse(cartoonize(svg(body)))
    (r,) = [r for r in rects(root) if r.get("rx")]
    assert num(r, "x") == near(50 - width_of("$"))
    assert num(r, "x") + num(r, "width") == near(95 + width_of("K"))


def test_wrapped_lines_get_one_rect_each() -> None:
    body = (
        f'<text x="0" font-size="11" font-family="{FONT}">'
        '<tspan x="0" y="20">first line</tspan>'
        '<tspan x="0" y="34">second</tspan></text>'
    )
    root = parse(cartoonize(svg(body)))
    assert len([r for r in rects(root) if r.get("rx")]) == 2


def test_superscript_run_shares_its_line_with_the_big_number() -> None:
    body = (
        f'<text x="0" y="30" font-family="{FONT}">'
        '<tspan y="20" font-size="11">$</tspan>'
        '<tspan y="30" font-size="24">3,744</tspan></text>'
    )
    root = parse(cartoonize(svg(body)))
    (r,) = [r for r in rects(root) if r.get("rx")]
    assert num(r, "width") == near(width_of("$", 11) + width_of("3,744", 24))


def test_empty_text_leaves_nothing() -> None:
    root = parse(cartoonize(svg(text_run(0, 10, "   "))))
    assert not [r for r in rects(root) if r.get("rx")]
    assert not list(root.iter(f"{NS}text"))


def test_dark_page_uses_white_ink() -> None:
    root = parse(cartoonize(svg(text_run(0, 20, "Night"), bg="#000000")))
    (r,) = [r for r in rects(root) if r.get("rx")]
    assert r.get("fill") == "#333333"


def test_missing_page_background_raises() -> None:
    bare = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
        f"{text_run(0, 5, 'x')}</svg>"
    )
    with pytest.raises(RenderError, match="data-dbt-page-background"):
        cartoonize(bare)


def test_malformed_svg_raises_render_error() -> None:
    with pytest.raises(RenderError, match="not well-formed"):
        cartoonize("<svg><g></svg>")


def test_root_dimensions_survive() -> None:
    root = parse(cartoonize(svg(text_run(0, 20, "x"))))
    assert root.get("width") == "400"
    assert root.get("height") == "200"
    assert root.get("viewBox") == "0 0 400 200"


def test_chart_furniture_is_removed_and_marks_stay() -> None:
    body = (
        '<g class="mark-rect role-mark marks"><path d="M0,0h5v5h-5Z" fill="#4f89c0"/></g>'
        '<g class="mark-text role-axis-label"><text x="0" y="9" font-size="11">1</text></g>'
        '<g class="mark-rule role-axis-tick"><line x1="0" x2="0" y1="0" y2="5"/></g>'
        '<g class="mark-rule role-axis-grid"><line x1="0" x2="9" y1="0" y2="0"/></g>'
        '<g class="mark-group role-axis-domain"><line x1="0" x2="9" y1="0" y2="0"/></g>'
        '<g class="mark-group role-legend"><rect width="9" height="9"/></g>'
        f'<a class="dbt-footer-link"><text x="0" y="0" font-size="9" font-family="{FONT}">dbt</text></a>'
    )
    out = cartoonize(svg(body))
    assert 'class="mark-rect role-mark marks"' in out
    assert 'fill="#4f89c0"' in out
    assert "role-axis-domain" in out
    for gone in ("role-axis-label", "role-axis-tick", "role-axis-grid", "role-legend"):
        assert gone not in out
    assert "dbt-footer-link" not in out
    assert "<text" not in out


def test_variable_control_becomes_one_placeholder_at_its_box() -> None:
    body = (
        '<g data-dbt-variables="true"><g data-dbt-variables-static="true">'
        '<g data-dbt-variable="region" data-dbt-x="120" data-dbt-y="4" '
        'data-dbt-width="90" data-dbt-height="36">'
        f"{text_run(120, 22, 'Region:')}"
        '<rect data-dbt-field="input" x="160" y="6" width="50" height="32" fill="transparent"/>'
        f"{text_run(168, 22, 'All')}"
        "</g></g></g>"
    )
    root = parse(cartoonize(svg(body)))
    assert not list(root.iter(f"{NS}text"))
    (group,) = [g for g in root.iter(f"{NS}g") if g.get("data-dbt-variables") == "true"]
    placeholders = [r for r in group.iter(f"{NS}rect") if r.get("fill") == GRAY]
    assert len(placeholders) == 1
    p = placeholders[0]
    assert num(p, "x") == 120
    assert num(p, "width") == 90
    assert num(p, "y") >= 4 and num(p, "y") + num(p, "height") <= 40
    assert not any(g.get("data-dbt-variable") for g in root.iter(f"{NS}g"))


def test_font_faces_are_stripped_and_empty_styles_dropped() -> None:
    body = (
        "<defs><style type='text/css'>"
        "@font-face { font-family: 'X'; src: url('data:font/woff2;base64,AAAA'); }"
        "</style>"
        "<style>@font-face { font-family: 'Y'; src: url(y.woff2); } .keep { fill: red; }</style>"
        "</defs>"
    )
    out = cartoonize(svg(body))
    assert "@font-face" not in out
    assert "base64" not in out
    assert ".keep" in out
    assert out.count("<style") == 1


# ---- tables ---------------------------------------------------------------

ACCENT_MARK = (
    '<g class="mark-rect role-mark marks">'
    '<path d="M0,0h5v5h-5Z" fill="rgb(200, 223, 246)"/>'
    '<path d="M0,0h5v5h-5Z" fill="rgb(31, 90, 160)"/></g>'
)


def cell(col: int, x: float, y: float, s: str, **attrs: str) -> str:
    return text_run(x, y, s, **{"data-col": str(col), "text-anchor": "start", **attrs})


def number_cell(col: int, right: float, y: float, value: float, **attrs: str) -> str:
    shown = f"{value:g}"
    a = " ".join(f'{k}="{v}"' for k, v in attrs.items())
    return (
        f'<text data-col="{col}" data-value="{value}" y="{y}" font-size="11" '
        f'font-family="{FONT}" fill="#222222" {a}>'
        f'<tspan x="{right}" text-anchor="end">{shown}</tspan></text>'
    )


def table(rows: str, extra: str = "") -> str:
    return (
        '<g class="dbt-chart" data-chart-type="table"><g transform="translate(16, 16)">'
        f"{rows}</g></g>{extra}"
    )


def blocks(root: ET.Element) -> list[ET.Element]:
    return [r for r in rects(root) if r.get("rx")]


def test_text_cells_become_equal_width_blocks_per_column() -> None:
    rows = (
        text_run(8, 20, "Name", **{"font-weight": "600"})
        + cell(0, 8, 50, "Alpha")
        + cell(0, 8, 74, "A much longer account name")
        + cell(0, 8, 98, "Mid length")
    )
    root = parse(cartoonize(svg(table(rows))))
    body_blocks = [b for b in blocks(root) if num(b, "y") > 40]
    assert len(body_blocks) == 3
    assert len({round(num(b, "width"), 3) for b in body_blocks}) == 1
    assert len({num(b, "x") for b in body_blocks}) == 1
    assert num(body_blocks[0], "x") == near(8)
    longest = width_of("A much longer account name")
    assert num(body_blocks[0], "width") == near(0.55 * longest)
    assert len(blocks(root)) == 4


def test_numeric_cells_become_value_bars_scaled_to_the_column_max() -> None:
    rows = (
        ACCENT_MARK
        + cell(0, 8, 50, "Alpha")
        + number_cell(1, 200, 50, 100)
        + cell(0, 8, 74, "Beta")
        + number_cell(1, 200, 74, 50)
    )
    root = parse(cartoonize(svg(table(rows))))
    bars = sorted(
        (b for b in blocks(root) if b.get("fill") != GRAY), key=lambda b: num(b, "y")
    )
    assert len(bars) == 2
    assert num(bars[0], "width") == near(2 * num(bars[1], "width"))
    # Both bars end where the column ends.
    right = num(bars[0], "x") + num(bars[0], "width")
    assert right <= 200 + 1e-6
    # darkest saturated mark color mixed 0.6 into white
    expected = tuple(round(c * 0.6 + 255 * 0.4) for c in (31, 90, 160))
    assert bars[0].get("fill") == "#" + "".join(f"{c:02x}" for c in expected)


def test_value_bars_fall_back_to_the_placeholder_gray_without_saturated_marks() -> None:
    rows = number_cell(1, 200, 50, 10) + number_cell(1, 200, 74, 5)
    root = parse(cartoonize(svg(table(rows))))
    assert {b.get("fill") for b in blocks(root)} == {GRAY}


def test_cell_on_its_own_fill_keeps_a_block_not_a_bar() -> None:
    fill = '<rect x="150" y="62" width="60" height="24" fill="#FFD0D0"/>'
    stripe = '<rect x="0" y="38" width="260" height="24" fill="#F7F8FA"/>'
    rows = (
        ACCENT_MARK
        + stripe
        + fill
        + cell(0, 8, 50, "Alpha")
        + number_cell(1, 200, 50, 100)
        + cell(0, 8, 74, "Beta")
        + number_cell(1, 200, 74, 100)
    )
    root = parse(cartoonize(svg(table(rows))))
    out = ET.tostring(root, encoding="unicode")
    assert 'fill="#FFD0D0"' in out and 'fill="#F7F8FA"' in out
    numeric = [b for b in blocks(root) if num(b, "x") > 100]
    by_y = sorted(numeric, key=lambda b: num(b, "y"))
    assert by_y[0].get("fill") != GRAY  # on the stripe only: still a value bar
    assert by_y[1].get("fill") == GRAY  # on its own fill: block


def test_chromatic_cell_text_keeps_its_color_in_the_block() -> None:
    rows = cell(0, 8, 50, "Late", fill="#D93025") + cell(0, 8, 74, "Fine")
    root = parse(cartoonize(svg(table(rows))))
    late, fine = sorted(blocks(root), key=lambda b: num(b, "y"))
    expected = tuple(round(c * 0.75 + 255 * 0.25) for c in (0xD9, 0x30, 0x25))
    assert late.get("fill") == "#" + "".join(f"{c:02x}" for c in expected)
    assert fine.get("fill") == GRAY


def test_glyph_tspan_becomes_a_colored_circle() -> None:
    body = (
        f'<text data-col="0" x="8" y="50" font-size="11" font-family="{FONT}" '
        'fill="#222222" text-anchor="start">'
        '<tspan fill="#1E8E3E">▲ </tspan>Up</text>'
    )
    root = parse(cartoonize(svg(table(body))))
    (circle,) = list(root.iter(f"{NS}circle"))
    assert circle.get("fill") == "#1E8E3E"
    assert not list(root.iter(f"{NS}text"))
    (block,) = blocks(root)
    assert num(block, "x") >= num(circle, "cx")


def test_table_keeps_stripes_swatches_and_sparks() -> None:
    body = (
        '<rect x="0" y="38" width="260" height="24" fill="#F7F8FA"/>'
        '<g transform="translate(4, 40)"><rect width="10" height="10" fill="#aa3355"/></g>'
        '<g><polyline points="0,0 5,5 9,2" fill="none" stroke="#336699"/></g>'
        + cell(0, 30, 50, "Alpha")
    )
    out = cartoonize(svg(table(body)))
    for kept in ('fill="#F7F8FA"', 'fill="#aa3355"', "<polyline"):
        assert kept in out


def test_non_table_charts_are_untouched_by_table_rules() -> None:
    body = (
        '<g class="dbt-chart" data-chart-type="kpi">' + cell(0, 8, 50, "Big") + "</g>"
    )
    root = parse(cartoonize(svg(body)))
    (b,) = blocks(root)
    assert num(b, "width") == near(width_of("Big"))
    assert re.fullmatch(r"#[0-9a-f]{6}", b.get("fill") or "")


# ---- the board's own typeface, canvases, measurement, and bad input -------------


def test_text_with_no_font_family_uses_the_boards_default_font() -> None:
    body = '<text x="10" y="30" font-size="11" fill="#222">Tab label</text>'
    root = parse(cartoonize(svg(body, root_extra=f'data-dbt-font-family="{FONT}"')))
    (r,) = blocks(root)
    assert num(r, "width") == near(width_of("Tab label"))


def test_text_with_no_font_family_and_no_board_font_raises() -> None:
    body = '<text x="10" y="30" font-size="11">Tab label</text>'
    with pytest.raises(RenderError, match="font-family"):
        cartoonize(svg(body))


@pytest.mark.parametrize("background", ["transparent", "none"])
def test_transparent_page_draws_translucent_ink(background: str) -> None:
    rows = ACCENT_MARK + cell(0, 8, 50, "Alpha") + number_cell(1, 200, 50, 100)
    root = parse(cartoonize(svg(table(rows) + text_run(0, 20, "T"), bg=background)))
    text_block, bar = sorted(
        (b for b in blocks(root) if num(b, "x") != 0), key=lambda b: num(b, "x")
    )[:2]
    assert text_block.get("fill") == "#000000"
    assert text_block.get("fill-opacity") == "0.2"
    assert bar.get("fill") == "#1f5aa0"
    assert bar.get("fill-opacity") == "0.6"


def test_opaque_page_draws_opaque_paint() -> None:
    root = parse(cartoonize(svg(text_run(0, 20, "x"))))
    assert all(r.get("fill-opacity") is None for r in rects(root))


def test_ink_follows_the_wcag_crossover_not_a_luminance_midpoint() -> None:
    # #777777 has more contrast against black ink than white, though its
    # luma is below 0.5: placeholder is 20% black over it.
    root = parse(cartoonize(svg(text_run(0, 20, "Mid"), bg="#777777")))
    (r,) = blocks(root)
    assert r.get("fill") == "#5f5f5f"


def test_unparseable_page_background_raises() -> None:
    with pytest.raises(RenderError, match="data-dbt-page-background"):
        cartoonize(svg(text_run(0, 20, "x"), bg="banana-split"))


def test_numeric_text_is_measured_with_the_renderers_tabular_font() -> None:
    stack = f"'{DBT_SANS_TABULAR_FONT_FAMILY}', Inter, system-ui, sans-serif"
    body = text_run(10, 30, "1,234,567.50", **{"font-family": stack})
    (r,) = blocks(parse(cartoonize(svg(body))))
    expected = get_font_measurer(stack, numeric=True).measure("1,234,567.50", 11)
    assert num(r, "width") == near(expected)
    assert expected != pytest.approx(width_of("1,234,567.50"), abs=0.5)


def test_prose_carrying_attributes_are_stripped_and_structure_kept() -> None:
    body = (
        '<g class="dbt-chart" data-chart-id="rev" data-chart-type="bar" '
        'data-chart-title="Revenue" data-chart-notes="Secret caveat">'
        '<path data-dbt-series="Accessories" d="M0,0h5v5h-5Z" fill="#4f89c0"/></g>'
    )
    out = cartoonize(
        svg(
            body,
            root_extra='data-dbt-page-title="Quarterly Report" data-rendered-at="x"',
        )
    )
    for prose in ("Revenue", "Secret caveat", "Quarterly Report", "Accessories"):
        assert prose not in out
    assert 'data-chart-id="rev"' in out
    assert 'data-chart-type="bar"' in out


@pytest.mark.parametrize(
    ("attrs", "message"),
    [
        ({"text-anchor": "sideways"}, "text-anchor"),
        ({"font-weight": "bolder"}, "font-weight"),
        ({"font-weight": "heavy"}, "font-weight"),
        ({"font-size": "1.5rem"}, "length"),
    ],
)
def test_unsupported_text_properties_raise_render_error(
    attrs: dict[str, str], message: str
) -> None:
    with pytest.raises(RenderError, match=message):
        cartoonize(svg(text_run(0, 20, "x", **attrs)))


@pytest.mark.parametrize("value", ["abc", "nan", "inf"])
def test_malformed_or_non_finite_data_value_raises_render_error(value: str) -> None:
    rows = cell(0, 8, 50, "n/a", **{"data-value": value})
    with pytest.raises(RenderError, match="data-value"):
        cartoonize(svg(table(rows)))


def test_non_integer_column_index_raises_render_error() -> None:
    with pytest.raises(RenderError, match="data-col"):
        cartoonize(svg(table(text_run(8, 50, "x", **{"data-col": "left"}))))


def test_variable_control_with_a_bad_box_raises_render_error() -> None:
    body = (
        '<g data-dbt-variable="r" data-dbt-x="left" data-dbt-y="4" '
        'data-dbt-width="90" data-dbt-height="36"></g>'
    )
    with pytest.raises(RenderError, match="data-dbt-x"):
        cartoonize(svg(body))


def test_em_length_without_a_font_size_raises_render_error() -> None:
    body = f'<text x="1em" y="20" font-family="{FONT}">x</text>'
    with pytest.raises(RenderError, match="em length"):
        cartoonize(svg(body))


def test_a_cell_of_only_glyphs_draws_dots_without_raising() -> None:
    body = (
        f'<text data-col="0" x="8" y="50" font-size="11" font-family="{FONT}" '
        'fill="#222222"><tspan fill="#1E8E3E">▲</tspan></text>'
    )
    root = parse(cartoonize(svg(table(body + cell(1, 80, 50, "Up")))))
    assert len(list(root.iter(f"{NS}circle"))) == 1
    assert len(blocks(root)) == 1


def test_href_namespace_serializes_with_its_xlink_prefix() -> None:
    body = '<use xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="#a"/>'
    out = cartoonize(svg(body))
    assert "xlink:href" in out
    assert "ns0" not in out
