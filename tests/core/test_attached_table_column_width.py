"""Regression: attached-table column widths must reserve the table
renderer's actual cell padding, not a hardcoded stand-in.

``build_attached_table_columns`` sizes columns before the table renderer
lays out cells. If its padding assumption disagrees with the padding the
table renderer actually applies (``table.column_layout.cell_padding``),
a column can end up ~1px too narrow — enough for ``wrap_text_precise`` to
wrap an atomic label like "CONMEBOL" or "41%" that would otherwise fit.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.chart.pie_attachment import (
    build_attached_table_columns,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.font_measure import get_font_measurer
from dbt_charts.core.render.chart.vega_lite import render_chart


def test_column_width_reserves_real_theme_cell_padding():
    """Each column width must accommodate its widest measured string plus
    the theme's actual cell_padding on both sides — the same padding the
    table renderer subtracts before wrapping (see the engine's
    ``content_w = col_widths[col] - cell_pad * 2``). A column narrower
    than that lets the renderer wrap text that has ample room.
    """
    resolved_style = resolve_chart_style_context(get_theme_style())
    table_style = resolved_style.table
    assert table_style.font.size is not None
    assert table_style.font.family is not None
    font_size = float(table_style.font.size)
    font_family = table_style.font.family
    cell_padding = table_style.column_layout.cell_padding

    rows = [
        {"swatch": "#000000", "share": "41%", "name": "CONMEBOL", "value": 41},
        {"swatch": "#ffffff", "share": "59%", "name": "UEFA", "value": 59},
    ]
    columns, _total_width = build_attached_table_columns(
        rows,
        None,
        table_style,
    )
    measurer = get_font_measurer(font_family)

    strings_by_column = {
        "share": [str(row["share"]) for row in rows],
        "name": [str(row["name"]) for row in rows],
    }
    for column_name, strings in strings_by_column.items():
        column = columns[column_name]
        assert isinstance(column.width, int), (
            f"{column_name} column width must be a concrete pixel int, got "
            f"{column.width!r}"
        )
        widest = max(measurer.measure(s, font_size) for s in strings)
        assert column.width >= widest + cell_padding * 2, (
            f"{column_name} column width {column.width} doesn't reserve the "
            f"real cell padding ({cell_padding} per side) around its widest "
            f"label (measures {widest:.2f}px) — the table renderer will "
            "wrap text that has room to fit."
        )


def test_rendered_attached_table_does_not_wrap_atomic_labels(make_chart):
    """End-to-end regression for the reported bug: a donut small enough to
    force the attached table (tiny tier, width < 360) must not wrap "41%"
    into "41"/"%" or "CONMEBOL" into "CONMEBO"/"L".

    Wrapped cells render their lines as sibling ``<tspan>`` elements inside
    one ``<text>``, which breaks the literal label string
    across markup. An intact, unwrapped label survives as a contiguous
    substring; a wrapped one does not. Slices the SVG to the table region
    (starting at the first swatch ``rx="3"`` rect, which the donut's own
    arc marks never emit) so a "CONMEBOL"/"41%" match in the donut's own
    aria-label text doesn't mask a real wrap in the table cells.
    """
    chart = make_chart("pie", x="series", y="value")
    data = [
        {"series": "CONMEBOL", "value": 41},
        {"series": "UEFA", "value": 59},
    ]
    rs, ctx = resolve_style_and_context(get_theme_style())
    svg = render_chart(chart, rs, ctx, data, format="svg", width=300.0)

    table_region = svg[svg.index('rx="3"') :]
    assert "CONMEBOL" in table_region, (
        "Expected the intact 'CONMEBOL' label in the rendered attached "
        "table — a wrapped cell would split it across <tspan> elements. "
        f"Table region: {table_region[:500]!r}"
    )
    assert "41%" in table_region, (
        "Expected the intact '41%' share label in the rendered attached "
        "table — a wrapped cell would split it across <tspan> elements. "
        f"Table region: {table_region[:500]!r}"
    )
