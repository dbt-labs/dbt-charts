"""Object titles (chart/table/spark) pick their size by width only.

Object titles map outer card width to an H-slot in ``style.title.sizes``.
Board nesting does not affect the result.

The contract is enforced both at the helper level (``chart_title_spec``
takes no ``level`` parameter) and at the renderer level (chart/table/spark
emit matching titles at matched outer widths). The renderer-level tests are
the ones that catch the regression class where two adjacent objects at the
same card position end up in different width tiers — the helper alone is
too narrow a surface to prove parity.
"""

from __future__ import annotations

import html
import re
from typing import Any

from pydantic import TypeAdapter

from dbt_charts.core.compile.config import (
    get_config,
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
    resolve_style_and_context,
)
from dbt_charts.core.render.chart.table import render_table_svg


def _board_style():
    return resolve_style(get_theme_style())


def _charts():
    return resolve_chart_style_context(get_theme_style())


def _make_chart(chart_type: str, title: str):
    from dbt_charts.core.compile.models.query.normalized import SqlQuery

    query = SqlQuery(sql="SELECT 1", source="test_db")
    kwargs: dict[str, Any] = {
        "id": f"{chart_type}_test",
        "type": chart_type,
        "title": title,
        "query": query,
        "query_name": "q",
    }
    if chart_type == "bar":
        kwargs["x"] = "month"
        kwargs["y"] = "revenue"
    elif chart_type == "spark_bar":
        kwargs["x"] = "v"
        kwargs["y"] = "k"
    return TypeAdapter(Chart).validate_python(dict(**kwargs))


class TestChartTitleSpecAtTier:
    """``chart_title_spec`` returns the right H slot for each width tier.

    Mapping (preserving the existing tier ladder):
        tiny    → H5
        narrow  → H3
        medium  → H2
        wide    → H2
    """

    def test_tiny_picks_h5_slot(self) -> None:
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec

        get_config()  # ensure settings initialized
        size, _, _ = chart_title_spec(300.0, chart_style_context=_charts())
        assert size == int(get_theme_style().title.sizes[4])  # H5

    def test_narrow_picks_h3_slot(self) -> None:
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec

        get_config()  # ensure settings initialized
        size, _, _ = chart_title_spec(400.0, chart_style_context=_charts())
        assert size == int(get_theme_style().title.sizes[2])  # H3

    def test_medium_picks_h2_slot(self) -> None:
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec

        get_config()  # ensure settings initialized
        size, _, _ = chart_title_spec(720.0, chart_style_context=_charts())
        assert size == int(get_theme_style().title.sizes[1])  # H2

    def test_wide_picks_h2_slot(self) -> None:
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec

        get_config()  # ensure settings initialized
        size, _, _ = chart_title_spec(1152.0, chart_style_context=_charts())
        assert size == int(get_theme_style().title.sizes[1])  # H2


class TestRendererTitleParityAcrossObjectTypes:
    """Chart, table, and spark titles agree at matched outer widths.

    Renderer-level integration: extract the title element from the rendered
    SVG of each object type and assert size + family match. This is what
    catches the audit's 576-vs-544 regression (where two adjacent objects
    ended up in different width tiers because they each computed their
    title-basis width differently).
    """

    _data = [
        {"month": "Jan", "revenue": 100, "k": "A", "v": 5},
        {"month": "Feb", "revenue": 220, "k": "B", "v": 8},
    ]

    def _extract_title_font(self, svg: str, title_text: str) -> tuple[int, str] | None:
        """Return (font_size, font_family) of the rendered title text, or None.

        Skips chart-wrapper attributes that contain the same text (e.g.,
        ``data-chart-title``, ``aria-label``) by anchoring on text content
        appearing between SVG tags.
        """
        # Search for the title appearing as text content (preceded by ">"
        # and followed by "<"), not as an attribute value.
        match = re.search(rf">([^<]*\b{re.escape(title_text)}\b[^<]*)<", svg)
        if not match:
            return None
        idx = match.start()
        t = svg.rfind("<text", 0, idx)
        if t < 0:
            return None
        excerpt = svg[t:idx]
        fs = re.search(r'font-size="([0-9.]+)(?:px)?"', excerpt)
        ff = re.search(r'font-family="([^"]+)"', excerpt)
        if not (fs and ff):
            return None
        return int(float(fs.group(1))), html.unescape(ff.group(1))

    def test_chart_table_spark_match_at_medium_outer_width(self) -> None:
        """At the same outer card width, all three object renderers emit
        the same title font-size and font-family. Width is medium tier
        (~720px → H2). The 576-vs-544 audit regression would surface here
        as a size mismatch between chart and table.
        """
        from dbt_charts.core.render.chart.spark_bar import render_spark_bar_svg
        from dbt_charts.core.render.chart.vega_lite import render_chart

        board_rs, board_ctx = resolve_style_and_context(get_theme_style())
        outer_width = 720.0
        # Single-token title that is idempotent under Chicago title-case,
        # so search-by-substring works regardless of theme.title.font.case.
        title = "Apex"

        # All three renderers receive ``width`` as the inset; each derives the
        # outer-card width internally from frame.card_padding for title-tier
        # selection. Passing ``width=outer_width`` here means leaves compute
        # outer = outer_width + 2*card_padding — slightly larger than the test's
        # nominal "medium" 720, but identical across all three, so the parity
        # contract still holds (same H slot for chart, table, spark).
        table_chart_v2 = resolve(
            _make_chart("table", title),
            self._data,
            chart_style_context=board_ctx,
        )
        spark_chart_v2 = resolve(
            _make_chart("spark_bar", title),
            self._data,
            chart_style_context=board_ctx,
        )
        chart_svg = render_chart(
            _make_chart("bar", title),
            board_rs,
            board_ctx,
            self._data,
            format="svg",
            width=outer_width,
        )
        table_svg = render_table_svg(
            table_chart_v2,
            self._data,
            outer_width,
            board_style=board_rs,
        )
        spark_svg = render_spark_bar_svg(
            spark_chart_v2,
            self._data,
            width=outer_width,
            board_style=board_rs,
        )

        chart_title = self._extract_title_font(chart_svg, title)
        table_title = self._extract_title_font(table_svg, title)
        spark_title = self._extract_title_font(spark_svg, title)

        assert chart_title is not None, "Chart title not found in rendered SVG"
        assert table_title is not None, "Table title not found in rendered SVG"
        assert spark_title is not None, "Spark title not found in rendered SVG"

        # Same size, same family across all three at matched outer width.
        assert chart_title[0] == table_title[0] == spark_title[0], (
            f"Object title sizes disagree: chart={chart_title[0]} "
            f"table={table_title[0]} spark={spark_title[0]}"
        )
        # Compare the leading family token (each renderer may emit a slightly
        # different fallback chain or "Inter Variable" vs "Inter"; the
        # primary family is what's visible).
        chart_primary = chart_title[1].split(",")[0].strip()
        table_primary = table_title[1].split(",")[0].strip()
        spark_primary = spark_title[1].split(",")[0].strip()
        # Vega may use the registered VL font name "Inter Variable" where SVG
        # paths use "Inter"; both resolve to the same TTF (see font_support).
        # Normalize for comparison.
        chart_norm = chart_primary.replace("Inter Variable", "Inter")
        table_norm = table_primary.replace("Inter Variable", "Inter")
        spark_norm = spark_primary.replace("Inter Variable", "Inter")
        assert chart_norm == table_norm == spark_norm, (
            f"Object title families disagree (primary token): "
            f"chart={chart_primary} table={table_primary} spark={spark_primary}"
        )
