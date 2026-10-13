"""Regression tests for theme-aware chart_title_spec.

Bug (pre-fix): ``chart_title_spec`` read the global ``get_config()`` directly,
which is the global default config (stark). For any non-default theme the
function returned the *default* theme's body/title families regardless of
which theme the board was actually using. Two symptoms compounded:

1. Chart rendering path. Width-aware ``chart_title_spec`` correctly demoted
   to body family at narrow widths,
   but the style overlay applied after it carried the cream serif
   delta and clobbered the narrow-tier body family — narrow editorial chart
   titles rendered serif when they should be sans.

2. Table path (``render_table_svg`` and ``compute_table_title_block_layout``).
   ``chart_title_spec`` returned the default theme's Inter for wide widths
   on every theme. The table path has no overlay step to recover the
   editorial title-slot family — wide editorial table titles rendered sans
   when they should be serif.

These tests exercise the cream theme end-to-end. They fail before
the fix and pass after it.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import BarChart, TableChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)


def _editorial_cream_charts():
    """Return the cream theme's ChartStyleContext."""
    return resolve_chart_style_context(get_theme_style("paper"))


def _editorial_cream_resolved():
    """Return the cream theme's full ResolvedStyle."""
    return resolve_style(get_theme_style("paper"))


class TestChartTitleSpecThemeAware:
    """chart_title_spec reads the *active* resolved style, not the global default."""

    def test_narrow_editorial_returns_body_family(self):
        """At narrow width (< 560px) under cream, the body family
        (Inter) must win — narrow-tier demotion preserves legibility regardless
        of the title-slot family the theme defines."""
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec

        charts = _editorial_cream_charts()
        # 390px is what the right-rail charts in dundersign-commercial-finance
        # actually receive.
        _, _, family = chart_title_spec(390.0, chart_style_context=charts)
        assert "Inter" in family
        assert "Source Serif" not in family, (
            f"narrow editorial chart title must use body family (Inter), got {family!r}"
        )

    def test_medium_editorial_returns_title_family(self):
        """At medium width (560–1099px) under cream, the title-slot
        family (Source Serif 4) must win."""
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec

        charts = _editorial_cream_charts()
        _, _, family = chart_title_spec(770.0, chart_style_context=charts)
        assert "Source Serif" in family, (
            f"medium editorial chart title must use title-slot family "
            f"(Source Serif 4), got {family!r}"
        )

    def test_wide_editorial_returns_title_family(self):
        """At wide width (≥1100px) under cream, the title-slot
        family (Source Serif 4) must win."""
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec

        charts = _editorial_cream_charts()
        # 1392px is what the bottom accounts table in
        # dundersign-commercial-finance actually receives.
        _, _, family = chart_title_spec(1392.0, chart_style_context=charts)
        assert "Source Serif" in family, (
            f"wide editorial chart title must use title-slot family "
            f"(Source Serif 4), got {family!r}"
        )


class TestVegaLiteTitleEditorialCream:
    """Emitted Vega-Lite spec carries the right title font under cream.

    The chart path's bug: even when chart_title_spec returned the right family,
    the board-level title config (baked once into effective_vega_config) applied
    afterward and clobbered narrow-tier body families. After the fix, the
    width-aware ``set_chart_title`` call applies last and the emitted spec
    carries the width-appropriate family.
    """

    _data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]

    def _make_chart(self, title: str = "Revenue"):
        from dbt_charts.core.compile.models.chart.normalized import BarChart
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        return BarChart(
            id="bar_test",
            type="bar",
            title=title,
            x="month",
            y="revenue",
            query=SqlQuery(sql="SELECT 1", source="test_db"),
        )

    def test_narrow_editorial_vega_chart_title_is_body_family(self):
        from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

        spec = generate_vega_lite_spec(
            self._make_chart(),
            data=self._data,
            width=390.0,
            board_style=_editorial_cream_resolved(),
            chart_style_context=_editorial_cream_charts(),
        )
        title_font = str(spec.get("title", {}).get("font", ""))
        assert "Inter" in title_font, (
            f"narrow editorial Vega chart title must include Inter, got {title_font!r}"
        )
        assert "Source Serif" not in title_font, (
            f"narrow editorial Vega chart title must not be Source Serif 4, "
            f"got {title_font!r}"
        )

    def test_wide_editorial_vega_chart_title_is_title_family(self):
        from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

        spec = generate_vega_lite_spec(
            self._make_chart(),
            data=self._data,
            width=1392.0,
            board_style=_editorial_cream_resolved(),
            chart_style_context=_editorial_cream_charts(),
        )
        title_font = str(spec.get("title", {}).get("font", ""))
        assert "Source Serif" in title_font, (
            f"wide editorial Vega chart title must be Source Serif 4, "
            f"got {title_font!r}"
        )


class TestAuthoredTitleStyleWinsOverWidthAware:
    """Authored ``style.title.font.{size,weight}`` must still win over the
    width-aware defaults.

    The bug fix reorders ``apply_presentation_defaults`` so the width-aware
    ``font`` (family) applies AFTER the style overlay. Its ``fontSize`` and
    ``fontWeight`` keep the original ordering (applied BEFORE the style
    overlay) so authored overrides flow through. These tests pin that split
    behavior so a future refactor that "simplifies" the overlay back to a
    single application doesn't silently drop authored size/weight.
    """

    _data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]

    def _make_chart(
        self,
        *,
        font_size: float | None = None,
        font_weight: int | None = None,
    ):
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.models.style.authored import (
            BarChartStylePatch,
            TitleStylePatch,
        )

        font_patch: dict[str, float | int] = {}
        if font_size is not None:
            font_patch["size"] = font_size
        if font_weight is not None:
            font_patch["weight"] = font_weight
        return BarChart(
            id="bar_test",
            type="bar",
            title="Revenue",
            x="month",
            y="revenue",
            query=SqlQuery(sql="SELECT 1", source="test_db"),
            style=BarChartStylePatch(
                title=TitleStylePatch(font=font_patch) if font_patch else None
            ),
        )

    def test_authored_title_font_size_wins_at_narrow_width(self):
        """At a narrow width where the ramp would pick ~11px, an authored
        ``style.title.font.size = 30`` must still produce fontSize=30 in the
        emitted spec."""
        from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

        spec = generate_vega_lite_spec(
            self._make_chart(font_size=30),
            self._data,
            width=390.0,
        )
        title_size = spec.get("title", {}).get("fontSize")
        assert title_size == 30, (
            f"Authored title.font.size=30 must win over the width-aware ramp "
            f"at narrow widths; got fontSize={title_size!r}"
        )

    def test_authored_title_font_weight_wins_at_medium_width(self):
        """At a medium width an authored ``style.title.font.weight = 800``
        must produce fontWeight=800 in the emitted spec, not the theme's
        default weight."""
        from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

        spec = generate_vega_lite_spec(
            self._make_chart(font_weight=800),
            self._data,
            width=770.0,
        )
        title_weight = spec.get("title", {}).get("fontWeight")
        assert title_weight == 800, (
            f"Authored title.font.weight=800 must win over the width-aware "
            f"default at medium widths; got fontWeight={title_weight!r}"
        )


class TestTableTitleEditorialCream:
    """Emitted table SVG carries the right title font under cream."""

    _data = [{"col_a": "x", "col_b": 1}]

    def _make_chart(self, title: str = "Accounts"):
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        return TableChart(
            id="tbl_test",
            type="table",
            title=title,
            query=SqlQuery(sql="SELECT 1", source="test_db"),
        )

    def test_wide_editorial_table_title_is_serif(self):
        """Wide cream table title renders Source Serif 4.

        Pre-fix: render_table_svg called chart_title_spec(width, ...) and
        got back Inter (default theme's title family). The table path has no
        overlay step, so the wrong family ended up in the SVG.
        """
        from ._engine_render import render_table_svg

        board_rs = _editorial_cream_resolved()
        resolved = resolve(
            self._make_chart(),
            self._data,
            chart_style_context=_editorial_cream_charts(),
            width=1392.0,
        )
        svg = render_table_svg(
            resolved,
            self._data,
            width=1392.0,
            board_style=board_rs,
        )
        assert "Source Serif" in svg, (
            "wide cream table title must render in Source Serif 4 "
            "(title-slot family). Pre-fix bug: chart_title_spec read the "
            "default-theme config and returned Inter regardless of theme."
        )

    def test_narrow_editorial_table_title_is_sans(self):
        """Narrow cream table title renders Inter (body family).

        Scope the assertion to the title ``<text>`` element specifically.
        The table SVG embeds Inter elsewhere (cells, headers) under
        cream because the root body font stays Inter; an SVG-wide
        ``"Inter" in svg`` substring check would pass even if the title
        wrongly rendered serif. We pull the title element by its known
        ``font-size`` (from ``chart_title_spec`` at this width) and check
        the family attribute on that one element.
        """
        import re

        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec

        from ._engine_render import render_table_svg

        charts = _editorial_cream_charts()
        expected_size, _, _ = chart_title_spec(390.0, chart_style_context=charts)
        board_rs = _editorial_cream_resolved()
        resolved = resolve(
            self._make_chart(), self._data, chart_style_context=charts, width=390.0
        )
        svg = render_table_svg(
            resolved,
            self._data,
            width=390.0,
            board_style=board_rs,
        )
        title_text = re.search(
            rf'<text[^>]*font-size="{expected_size}"[^>]*font-family="([^"]+)"',
            svg,
        )
        assert title_text is not None, (
            f"Could not find narrow table title <text> element at "
            f"font-size={expected_size} in SVG. Head: {svg[:600]!r}"
        )
        family = title_text.group(1)
        assert "Inter" in family, (
            f"narrow cream table title must use Inter (body family "
            f"at narrow widths); got family={family!r}"
        )
        assert "Source Serif" not in family, (
            f"narrow cream table title must NOT use Source Serif 4 "
            f"(the title-slot family is reserved for medium/wide widths); "
            f"got family={family!r}"
        )
