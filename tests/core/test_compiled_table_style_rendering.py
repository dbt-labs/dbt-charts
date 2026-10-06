"""Table rendering reads from TableChartStyle directly."""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
    resolve_style_and_context,
)


def test_resolved_charts_style_table_is_compiled_table_style():
    """ChartStyleContext.table must be a TableChartStyle instance at runtime."""
    from dbt_charts.core.compile.models.style.theme import TableChartStyle

    ctx = resolve_chart_style_context(get_theme_style())
    assert isinstance(ctx.table, TableChartStyle)


def test_resolved_charts_table_has_nested_fields():
    """ChartStyleContext.table exposes TableChartStyle nested fields."""

    ctx = resolve_chart_style_context(get_theme_style())
    tc = ctx.table
    assert tc.font.size is not None
    assert tc.header.font.size is not None
    assert tc.row.height is not None


def test_render_table_svg_uses_compiled_table_style_font_size():
    """render_table_svg reads tc.font.size from TableChartStyle directly."""
    from dbt_charts.core.compile.models.primitives import FontStyle
    from dbt_charts.core.render.chart.table import render_table_svg as render_table_svg

    base = get_theme_style()
    seed = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "table": base.charts.table.model_copy(
                        update={"font": FontStyle(size=16.0)}
                    )
                }
            )
        }
    )
    chart = resolve(
        _make_chart(), [], chart_style_context=resolve_chart_style_context(seed)
    )
    svg = render_table_svg(
        chart,
        [{"Col": "Value"}],
        width=400,
        board_style=resolve_style(get_theme_style()),
    )
    assert 'font-size="16"' in svg


def test_render_table_svg_uses_compiled_table_header_height():
    """render_table_svg reads tc.header.height from TableChartStyle."""
    from dbt_charts.core.render.chart.table import render_table_svg as render_table_svg

    base = get_theme_style()
    seed = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "table": base.charts.table.model_copy(
                        update={
                            "header": base.charts.table.header.model_copy(
                                update={"height": 99.0}
                            )
                        }
                    )
                }
            )
        }
    )
    chart = resolve(
        _make_chart(), [], chart_style_context=resolve_chart_style_context(seed)
    )
    svg = render_table_svg(
        chart,
        [{"Col": "Value"}],
        width=400,
        board_style=resolve_style(get_theme_style()),
    )
    # Header height = 99 pushes the header rule to y≈98 (height - rule_width).
    # Without a header background rect, we confirm by finding the rule near y=99.
    import re

    rule_ys = [
        float(m.group(1))
        for m in re.finditer(r'<rect [^>]*y="([\d.]+)"[^>]*height="1(?:\.0)?"', svg)
    ]
    assert any(95 < y < 100 for y in rule_ys), (
        f"Expected header rule rect near y≈99 (header height), got {rule_ys}"
    )


def test_render_table_svg_uses_compiled_row_stripe_color():
    """render_table_svg reads tc.row.stripe.color from TableChartStyle."""
    from dbt_charts.core.compile.models.style.theme import TableRowStripeStyle
    from dbt_charts.core.render.chart.table import render_table_svg as render_table_svg

    base = get_theme_style()
    seed = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "table": base.charts.table.model_copy(
                        update={
                            "row": base.charts.table.row.model_copy(
                                update={"stripe": TableRowStripeStyle(color="#abcdef")}
                            )
                        }
                    )
                }
            )
        }
    )
    data = [{"Col": f"Row {i}"} for i in range(4)]
    chart = resolve(
        _make_chart(), [], chart_style_context=resolve_chart_style_context(seed)
    )
    svg = render_table_svg(
        chart,
        data,
        width=400,
        board_style=resolve_style(get_theme_style()),
    )
    assert "#abcdef" in svg


def test_calculate_data_aware_layout_uses_board_width_without_charts():
    """A chart-free layout retains the board maximum as its width."""
    import importlib
    from unittest.mock import MagicMock, patch

    from ._board_utils import make_test_board

    ls_module = importlib.import_module("dbt_charts.core.render.layout_sizing")

    resolved = resolve_style(get_theme_style())
    board = make_test_board(id="f1", resolved_style=resolved)
    executor = MagicMock()
    executor.execute_chart.return_value = []

    with (
        patch.object(ls_module, "calculate_layout_height", return_value=500.0),
        patch.object(ls_module, "calculate_layout_items"),
        patch.object(ls_module, "_align_all_cols_in_tree"),
    ):
        from dbt_charts.core.render.layout_sizing import calculate_data_aware_layout

        result_board, _ = calculate_data_aware_layout(
            board, executor, {}, render_first=False, pre_resolved={}
        )

    assert result_board.layout.width == resolved.frame.max_width


def _make_chart(style=None):
    from dbt_charts.core.compile.models.chart.normalized import TableChart

    return TableChart(id="test_chart", type="table", style=style)


def test_render_table_swatch_column_emits_rect():
    """A column configured with ``swatch: true`` renders cells as a small
    rounded SVG rect filled with the cell value (a CSS color string).

    The cell does NOT emit a ``<text>`` for the swatch column — the swatch
    rect IS the cell content. Sibling columns still render as text.
    """
    from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
    from dbt_charts.core.compile.models.style.authored import (
        TableChartStylePatch,
    )
    from dbt_charts.core.render.chart.table import render_table_svg as render_table_svg

    board_rs, board_ctx = resolve_style_and_context(get_theme_style())
    data = [
        {"swatch": "#3164a3", "name": "Enterprise"},
        {"swatch": "#779bc9", "name": "Mid-Market"},
    ]
    chart = resolve(
        _make_chart(
            style=TableChartStylePatch(
                columns={
                    "swatch": TableColumnConfig(swatch=True),
                    "name": TableColumnConfig(),
                }
            )
        ),
        [],
        chart_style_context=board_ctx,
    )
    svg = render_table_svg(
        chart,
        data,
        width=400,
        board_style=board_rs,
    )
    # Two swatches emitted with the row colors.
    assert 'fill="#3164a3"' in svg, (
        "swatch rect for first row missing fill='#3164a3': " + svg[:500]
    )
    assert 'fill="#779bc9"' in svg, "swatch rect for second row missing fill"
    # Rect must carry a non-zero rx (rounded corners, not a plain square).
    import re

    # Locate the <rect> for the first swatch and verify rx > 0 (rounded corners).
    swatch_rect_match = re.search(
        r'<rect\b[^>]*\bfill="#3164a3"[^>]*/>', svg
    ) or re.search(r'<rect\b(?=[^>]*\brx=)(?=[^>]*\bfill="#3164a3")[^>]*/>', svg)
    assert swatch_rect_match, "expected to find swatch rect with fill=#3164a3"
    rx_match = re.search(r'\brx="([\d.]+)"', swatch_rect_match.group(0))
    assert rx_match, f"swatch rect missing rx attribute: {swatch_rect_match.group(0)}"
    assert float(rx_match.group(1)) > 0, (
        f"swatch rx must be > 0 for visible rounded corners; got {rx_match.group(1)}"
    )
    # The swatch column must NOT emit the color string as text content.
    assert ">#3164a3<" not in svg, "swatch column must not render the hex as text"


def test_swatch_column_non_color_value_raises():
    """A swatch column whose cell is not a CSS color must raise
    ChartDataError, not silently render as text.

    No swatch color → no swatch. The author either pointed swatch: true at
    the wrong column or the data carries the wrong content; both are
    misconfigs worth surfacing loud.
    """
    import pytest

    from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
    from dbt_charts.core.compile.models.style.authored import (
        TableChartStylePatch,
    )
    from dbt_charts.core.diagnostics.chart_data import ChartDataError
    from dbt_charts.core.render.chart.table import render_table_svg as render_table_svg

    board_rs, board_ctx = resolve_style_and_context(get_theme_style())
    # First row swatch is a valid color; second row swatch is the row label
    # by mistake — author wired swatch: true to the wrong column.
    data = [
        {"swatch": "#3164a3", "name": "Enterprise"},
        {"swatch": "Mid-Market", "name": "Mid-Market"},
    ]
    chart = resolve(
        _make_chart(
            style=TableChartStylePatch(
                columns={
                    "swatch": TableColumnConfig(swatch=True),
                    "name": TableColumnConfig(),
                }
            )
        ),
        [],
        chart_style_context=board_ctx,
    )
    with pytest.raises(ChartDataError) as exc_info:
        render_table_svg(
            chart,
            data,
            width=400,
            board_style=board_rs,
        )
    msg = str(exc_info.value)
    assert "swatch" in msg.lower(), f"error must mention swatch: {msg}"
    assert "Mid-Market" in msg, f"error must surface the bad value: {msg}"


def test_swatch_column_pinned_in_demand_and_word_floor():
    """Swatch columns must not measure cell text in any column-width path.

    Three sibling paths read column content widths: _compute_wrap_layout
    (wrap inflation), measure_column_demands (auto-width allocation), and
    measure_column_word_floors (text-column floor). All three must skip
    swatch columns — a 7-char hex string would otherwise inflate a 24px
    swatch column to ~50–80px and shrink siblings.
    """
    from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
    from dbt_charts.core.font_measure import get_font_measurer
    from dbt_charts.core.render.chart.table_support import (
        measure_column_demands,
        measure_column_word_floors,
    )

    cfgs = {
        "swatch": TableColumnConfig(swatch=True),
        "name": TableColumnConfig(),
    }
    data = [{"swatch": "#3164a3", "name": "Enterprise"}]
    measurer = get_font_measurer("Inter Variable")
    cell_demands, _header_demands = measure_column_demands(
        columns=["swatch", "name"],
        column_configs=cfgs,
        data=data,
        measurer=measurer,
        header_measurer=measurer,
        font_size=11.0,
        header_font_size=11.0,
        cell_pad=6,
        column_when_rules={},
    )
    floors = measure_column_word_floors(
        columns=["swatch", "name"],
        column_configs=cfgs,
        data=data,
        measurer=measurer,
        header_measurer=measurer,
        font_size=11.0,
        header_font_size=11.0,
        cell_pad=6,
        column_when_rules={},
    )
    # Swatch demand/floor must be pinned to the rect width plus padding,
    # not the ~50px width of "#3164a3" rendered as text. Hex measurement
    # would land in the high 40s — anything <40 proves we're pinned.
    assert cell_demands["swatch"] < 40, (
        "swatch column demand inflated by hex-string text measurement. "
        f"got {cell_demands['swatch']} — expected pinned to the rect width"
    )
    assert floors["swatch"] < 40, (
        "swatch column word-floor inflated by hex-string text measurement. "
        f"got {floors['swatch']} — expected pinned to the rect width"
    )


def test_swatch_column_skipped_in_overflow_check():
    """``_has_overflow`` must skip swatch columns.

    A 24px-wide swatch column with a 7-char hex value would otherwise
    return True (hex ≈ 42px > 12px content area) and kick off the
    table-wide fit cascade (cell_pad shrink, font step-down to 11px).
    The cascade should only fire when a real text or numeric column
    overflows — swatch cells render as a 14px rect.
    """
    from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
    from dbt_charts.core.compile.models.primitives import FontStyle
    from dbt_charts.core.render.chart.table import _has_overflow

    cfgs = {"swatch": TableColumnConfig(swatch=True), "name": TableColumnConfig()}
    data = [
        {"swatch": "#3164a3", "name": "Enterprise"},
        {"swatch": "#779bc9", "name": "Mid-Market"},
    ]
    # Tight 24px swatch column would trigger overflow if hex were measured;
    # 200px name column comfortably fits "Enterprise" (~70px).
    col_widths = {"swatch": 24.0, "name": 200.0}
    font = FontStyle(size=11.0, family="Inter Variable")
    overflowed = _has_overflow(
        ["swatch", "name"],
        data,
        cfgs,
        col_widths,
        cell_pad=6,
        cell_font=font,
        header_font=font,
        wrap=False,
        formats=None,
        column_when_rules={},
    )
    assert overflowed is False, (
        "swatch column hex value tripped the overflow check; "
        "fit cascade would fire when only the rect needs to render"
    )


def test_swatch_column_skipped_in_wrap_layout():
    """Swatch columns must not contribute to wrap-driven row height.

    Regression: a narrow ``width: 24`` swatch column holds a 7-char hex
    string (e.g. ``#3164a3``). If ``_compute_wrap_layout`` treats the cell
    as text, the hex wraps to ~one-char-per-line and inflates row height to
    ~100px, which in turn truncates every-row-after-the-first under a
    bounded card height.
    """
    from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
    from dbt_charts.core.font_measure import get_font_measurer
    from dbt_charts.core.render.chart.table import _compute_wrap_layout

    column_configs = {
        "swatch": TableColumnConfig(swatch=True, width=24),
        "name": TableColumnConfig(width=120),
    }
    rows = [
        {"swatch": "#3164a3", "name": "Enterprise"},
        {"swatch": "#779bc9", "name": "Mid-Market"},
    ]
    col_widths = {"swatch": 24.0, "name": 120.0}
    heights, wrapped = _compute_wrap_layout(
        rows=rows,
        text_columns=["swatch", "name"],
        column_configs=column_configs,
        col_widths=col_widths,
        cell_pad=6,
        font_size=11,
        row_height=24,
        text_baseline_offset=4.0,
        measurer=get_font_measurer("Inter Variable"),
        column_when_rules={},
    )
    assert heights == [24, 24], (
        "swatch column inflated row heights — should be skipped entirely. "
        f"got heights={heights}"
    )
    for row_wraps in wrapped:
        assert "swatch" not in row_wraps, (
            f"swatch column produced wrapped lines: {row_wraps}"
        )


def test_hidden_header_zeros_header_demands():
    """When ``header.visible: false``, header_demands must be 0.

    Cell demands are unchanged (based on cell content only).  The header
    cannot grow a column via leftover budget when it is not visible.

    Concrete case: a 'category' column with cells like 'A', 'B', 'C' and
    header label 'category' (8 chars).  With the header visible,
    header_demands['category'] reflects the 8-char label width.  With the
    header hidden, header_demands['category'] is 0 so no budget is spent
    toward the label.
    """
    from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
    from dbt_charts.core.font_measure import get_font_measurer
    from dbt_charts.core.render.chart.table_support import measure_column_demands

    cfgs = {"category": TableColumnConfig()}
    data = [{"category": v} for v in ("A", "B", "C")]
    common = {
        "columns": ["category"],
        "column_configs": cfgs,
        "data": data,
        "measurer": get_font_measurer("Inter Variable"),
        "header_measurer": get_font_measurer("Inter Variable"),
        "font_size": 11.0,
        "header_font_size": 11.0,
        "cell_pad": 6,
    }
    cell_visible, header_visible = measure_column_demands(
        **common, header_visible=True, column_when_rules={}
    )
    cell_hidden, header_hidden = measure_column_demands(
        **common, header_visible=False, column_when_rules={}
    )

    # Cell demands are identical — they depend only on cell content.
    assert abs(cell_visible["category"] - cell_hidden["category"]) <= 0.5, (
        "Cell demands must be equal regardless of header visibility. "
        f"got {cell_visible['category']:.1f} vs {cell_hidden['category']:.1f}"
    )
    # Header demand is non-zero when header is visible, zero when hidden.
    assert header_visible["category"] > 0, (
        f"header_demands must be non-zero when header is visible; "
        f"got {header_visible['category']:.1f}"
    )
    assert header_hidden["category"] == 0.0, (
        "header_demands must be 0 when header is hidden; "
        f"got {header_hidden['category']:.1f}"
    )


def test_render_table_header_invisible_suppresses_header():
    """``style.header.visible: false`` removes the header row entirely.

    The column labels do NOT appear in the SVG and the header rule above
    the first data row is also suppressed. Useful for series-keyed tables
    (e.g. donut-attached tables) where column meanings are obvious from
    context and a header row reads as noise.
    """
    from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
    from dbt_charts.core.compile.models.style.authored import (
        TableChartStylePatch,
    )
    from dbt_charts.core.render.chart.table import render_table_svg as render_table_svg

    board_rs, board_ctx = resolve_style_and_context(get_theme_style())
    data = [
        {"name": "Annual", "value": "$25,200"},
        {"name": "Monthly", "value": "$3,615"},
    ]
    chart = resolve(
        _make_chart(
            style=TableChartStylePatch.model_validate(
                {
                    "header": {"visible": False},
                    "columns": {
                        "name": TableColumnConfig(label="Series"),
                        "value": TableColumnConfig(label="Value"),
                    },
                }
            )
        ),
        [],
        chart_style_context=board_ctx,
    )
    svg = render_table_svg(
        chart,
        data,
        width=400,
        board_style=board_rs,
    )
    # Header labels must not appear as text.
    assert ">Series<" not in svg, "header label 'Series' should be suppressed"
    assert ">Value<" not in svg, "header label 'Value' should be suppressed"
    # Data rows still render.
    assert ">Annual<" in svg
    assert ">Monthly<" in svg
