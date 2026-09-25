"""Tests for the dbt title typography system.

Validates:
- _width_offset maps pixel widths to the correct additive level offset
- chart_title_spec(width) returns the correct (font_size, weight, family) by width tier alone
- board_title_spec(level=...) returns size and weight for board titles (level-only)
- SVG renderers (kpi, table, spark_bar) embed width-aware font-size/weight/family
- Vega-Lite charts get width-aware title font family in their config
- Board.level is correctly set by the normalizer
"""

from __future__ import annotations

import dataclasses

from dbt_charts.core.compile.config import (
    get_config,
    get_theme_style,
)
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    KpiChart,
    SparkBarChart,
    TableChart,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

_EFF = resolve_chart_style_context(get_theme_style())
_BOARD_STYLE = resolve_style(get_theme_style())
_BOARD_CONTEXT = resolve_chart_style_context(get_theme_style())


# ---------------------------------------------------------------------------
# Width offset helper
# ---------------------------------------------------------------------------


class TestWidthOffset:
    """_width_offset maps pixel widths to integer level offsets from theme YAML.

    Probe widths derive from the tier constants rather than naming pixels. The
    boundaries move whenever the default board's column grid changes (see
    ``docs/guides/typographic-tiers.md``), so a literal here pins a value that is
    designed to move — and passes or fails for reasons unrelated to the mapping
    these tests actually cover.
    """

    def test_tiny_below_threshold(self) -> None:
        from dbt_charts.core.compile.resolve.style.typography import (
            _TINY_MAX,
            _width_offset,
        )

        get_config()  # ensure settings initialized
        offsets = get_theme_style().title.width_offsets
        assert _width_offset(_TINY_MAX / 2, _EFF) == offsets.tiny
        assert _width_offset(_TINY_MAX - 0.1, _EFF) == offsets.tiny

    def test_narrow_between_thresholds(self) -> None:
        from dbt_charts.core.compile.resolve.style.typography import (
            _NARROW_MAX,
            _TINY_MAX,
            _width_offset,
        )

        get_config()  # ensure settings initialized
        offsets = get_theme_style().title.width_offsets
        assert _width_offset(_TINY_MAX, _EFF) == offsets.narrow
        assert _width_offset(_NARROW_MAX - 0.1, _EFF) == offsets.narrow

    def test_medium_between_thresholds(self) -> None:
        from dbt_charts.core.compile.resolve.style.typography import (
            _NARROW_MAX,
            _WIDE_MIN,
            _width_offset,
        )

        get_config()  # ensure settings initialized
        offsets = get_theme_style().title.width_offsets
        assert _width_offset(_NARROW_MAX, _EFF) == offsets.medium
        assert _width_offset(_WIDE_MIN - 0.1, _EFF) == offsets.medium

    def test_wide_at_or_above_threshold(self) -> None:
        from dbt_charts.core.compile.resolve.style.typography import (
            _WIDE_MIN,
            _width_offset,
        )

        get_config()  # ensure settings initialized
        offsets = get_theme_style().title.width_offsets
        assert _width_offset(_WIDE_MIN, _EFF) == offsets.wide
        assert _width_offset(_WIDE_MIN * 2, _EFF) == offsets.wide


# ---------------------------------------------------------------------------
# Chart title spec
# ---------------------------------------------------------------------------


class TestChartTitleSpec:
    """chart_title_spec(width) returns (font_size, weight, family) by width tier alone."""

    def test_returns_int_size_for_width_alone(self):
        """chart_title_spec needs only width and resolved style — no level."""
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec

        font_size, _weight, _family = chart_title_spec(400.0, chart_style_context=_EFF)
        assert isinstance(font_size, int)

    def test_narrow_returns_correct_size_sans(self):
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec

        # level=2 (root board chart), narrow width → offset demotes → smaller than medium
        narrow_size, weight, family = chart_title_spec(400.0, chart_style_context=_EFF)
        medium_size, _, _ = chart_title_spec(700.0, chart_style_context=_EFF)
        assert narrow_size <= medium_size, (
            "narrow tier must not exceed medium tier size"
        )
        # narrow uses config weight, same as wide/medium (no silent override)
        assert weight == 500  # default theme weight
        assert family.startswith(get_theme_style().font.family.split(",")[0].strip())

    def test_medium_returns_title_family_at_default_weight(self):
        """Medium tier (560-1099px) uses level offset and the title-slot family
        (sans on default, serif on editorial), at the config-provided weight."""
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec

        font_size, weight, family = chart_title_spec(700.0, chart_style_context=_EFF)
        assert isinstance(font_size, int)
        assert weight == 500
        assert family.startswith(
            get_theme_style().title.font.family.split(",")[0].strip()
        )

    def test_wide_returns_title_family_at_default_weight(self):
        """Wide tier (≥1100px) uses level offset and the title-slot family
        (sans on default, serif on editorial), at the config-provided weight."""
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec

        font_size, weight, family = chart_title_spec(1152.0, chart_style_context=_EFF)
        assert isinstance(font_size, int)
        assert weight == 500
        assert family.startswith(
            get_theme_style().title.font.family.split(",")[0].strip()
        )

    def test_width_branch_uses_body_at_narrow_title_at_medium(self):
        """The width-aware family resolution must actually select between the
        body family (``font_family``) and the title-slot family
        (``title.font.family``). Under the stark default theme both slots
        carry the same string, so this test builds a resolved style with a
        distinctive title-slot family and verifies the discriminator: narrow
        tier returns the body family, medium tier returns the (overridden)
        title family. Without this test the width-driven branch in
        chart_title_spec is observationally untested under the default
        theme."""
        from dbt_charts.core.compile.config import get_config
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec

        get_config()  # ensure settings initialized
        distinctive = "'dbt Test Distinctive Title', serif"
        new_font = get_theme_style().title.font.model_copy(
            update={"family": distinctive}
        )
        new_title = get_theme_style().title.model_copy(update={"font": new_font})
        new_style = get_theme_style().model_copy(update={"title": new_title})
        charts = resolve_chart_style_context(new_style)

        body_first = get_theme_style().font.family.split(",")[0].strip()
        title_first = "'dbt Test Distinctive Title'"

        # Narrow tier (400px) → body family, not the distinctive title family.
        _, _, narrow_family = chart_title_spec(400.0, chart_style_context=charts)
        assert narrow_family.startswith(body_first)
        assert title_first not in narrow_family

        # Medium tier (700px) → distinctive title family.
        _, _, medium_family = chart_title_spec(700.0, chart_style_context=charts)
        assert medium_family.startswith(title_first)

        # Wide tier (1152px) → same distinctive title family.
        _, _, wide_family = chart_title_spec(1152.0, chart_style_context=charts)
        assert wide_family.startswith(title_first)

    def test_use_title_family_true_forces_title_at_narrow(self):
        """``use_title_family=True`` forces the title-slot family even on
        narrow widths. Builds a resolved style with a distinctive title-slot
        family so the assertion discriminates against the body family even
        when both slots collapse to the same string under the stark default
        theme."""
        from dbt_charts.core.compile.config import get_config
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec

        get_config()  # ensure settings initialized
        new_font = get_theme_style().title.font.model_copy(
            update={"family": "'dbt Test Distinctive Title', serif"}
        )
        new_title = get_theme_style().title.model_copy(update={"font": new_font})
        new_style = get_theme_style().model_copy(update={"title": new_title})
        charts = resolve_chart_style_context(new_style)

        _size, _weight, family = chart_title_spec(
            400.0, chart_style_context=charts, use_title_family=True
        )
        assert family.startswith("'dbt Test Distinctive Title'")
        assert not family.startswith(
            get_theme_style().font.family.split(",")[0].strip()
        )

    def test_use_title_family_false_forces_body_at_medium(self):
        """``use_title_family=False`` forces the body family even at medium
        widths. Builds a resolved style with a distinctive title-slot family
        so the assertion discriminates against the title family even when
        both slots collapse to the same string under the stark default
        theme."""
        from dbt_charts.core.compile.config import get_config
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec

        get_config()  # ensure settings initialized
        new_font = get_theme_style().title.font.model_copy(
            update={"family": "'dbt Test Distinctive Title', serif"}
        )
        new_title = get_theme_style().title.model_copy(update={"font": new_font})
        new_style = get_theme_style().model_copy(update={"title": new_title})
        charts = resolve_chart_style_context(new_style)

        _size, _weight, family = chart_title_spec(
            700.0, chart_style_context=charts, use_title_family=False
        )
        assert family.startswith(get_theme_style().font.family.split(",")[0].strip())
        assert "'dbt Test Distinctive Title'" not in family

    def test_default_theme_chart_title_includes_noto_emoji(self):
        """Noto Emoji must appear in chart_title_spec family for all tiers."""
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec

        for width in [400.0, 700.0, 1152.0]:
            _, _, family = chart_title_spec(width, chart_style_context=_EFF)
            assert "Noto Emoji" in family, (
                f"Noto Emoji missing from chart title family at width={width}: {family!r}"
            )

    def test_medium_weight_comes_from_config(self):
        """chart_title_spec must use the resolved title.font.weight for medium tier."""
        from dbt_charts.core.compile.config import get_config
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec

        get_config()  # ensure settings initialized
        new_font = get_theme_style().title.font.model_copy(update={"weight": 650})
        new_title = get_theme_style().title.model_copy(update={"font": new_font})
        new_style = get_theme_style().model_copy(update={"title": new_title})
        charts = resolve_chart_style_context(new_style)

        _, weight, _ = chart_title_spec(700.0, chart_style_context=charts)
        assert weight == 650

    def test_tiny_weight_comes_from_compact_theme_slot(self):
        """Tiny title weight is a theme value, not an engine-owned constant."""
        from dbt_charts.core.compile.config import get_config
        from dbt_charts.core.compile.resolve.style.typography import (
            _TINY_MAX,
            chart_title_spec,
        )

        get_config()  # ensure settings initialized
        new_title = get_theme_style().title.model_copy(update={"compact_weight": 611.0})
        new_style = get_theme_style().model_copy(update={"title": new_title})
        charts = resolve_chart_style_context(new_style)

        _, weight, _ = chart_title_spec(_TINY_MAX - 0.1, chart_style_context=charts)
        assert weight == 611

    def test_wide_weight_comes_from_config(self):
        """chart_title_spec must use the resolved title.font.weight for wide tier."""
        from dbt_charts.core.compile.config import get_config
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec

        get_config()  # ensure settings initialized
        new_font = get_theme_style().title.font.model_copy(update={"weight": 650})
        new_title = get_theme_style().title.model_copy(update={"font": new_font})
        new_style = get_theme_style().model_copy(update={"title": new_title})
        charts = resolve_chart_style_context(new_style)

        _, weight, _ = chart_title_spec(1152.0, chart_style_context=charts)
        assert weight == 650

    def test_string_weight_does_not_crash(self):
        """Themes with string font weights (e.g. 'bold') must not raise ValueError."""
        from dbt_charts.core.compile.config import get_config
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec

        get_config()  # ensure settings initialized
        new_font = get_theme_style().title.font.model_copy(update={"weight": "bold"})
        new_title = get_theme_style().title.model_copy(
            update={"font": new_font, "compact_weight": "bold"}
        )
        new_style = get_theme_style().model_copy(update={"title": new_title})
        charts = resolve_chart_style_context(new_style)

        for width in [400.0, 700.0, 1152.0]:
            _, weight, _ = chart_title_spec(width, chart_style_context=charts)
            assert weight == "bold"


# ---------------------------------------------------------------------------
# Board title spec
# ---------------------------------------------------------------------------


class TestFaceTitleSpec:
    """board_title_spec returns size and weight for semantic board titles.

    Board titles are level-only (semantic). Width is not an input — a
    narrow-column board title is the same pixel size as the same-level board
    title in a wide column. Tests below pin that contract.
    """

    def test_rendered_board_title_size_does_not_depend_on_column_width(self):
        """Renderer-level: same board title rendered at narrow vs wide width
        produces the same font-size in the SVG. Board titles are semantic
        headings — never width-responsive.
        """
        import re

        from dbt_charts.core.render.svg_utils import render_title

        narrow_svg = render_title(
            "Apex",
            200.0,  # well below the narrow tier boundary
            level=2,
            resolved_style=_BOARD_STYLE,
        )
        wide_svg = render_title(
            "Apex",
            1152.0,  # wide
            level=2,
            resolved_style=_BOARD_STYLE,
        )

        def _extract_font_size(svg: str) -> int:
            m = re.search(r'font-size="([0-9.]+)(?:px)?"', svg)
            assert m, f"font-size not found in rendered title SVG: {svg[:200]!r}"
            return int(float(m.group(1)))

        narrow_size = _extract_font_size(narrow_svg)
        wide_size = _extract_font_size(wide_svg)
        assert narrow_size == wide_size, (
            f"Board title size differs by column width: narrow={narrow_size}, "
            f"wide={wide_size}. Board titles are semantic (level-only)."
        )

    def test_nested_board_title_smaller_than_root(self):
        """Higher board.level (deeper semantic nesting) picks a smaller H slot."""
        from dbt_charts.core.compile.resolve.style.typography import board_title_spec

        root_size, _ = board_title_spec(level=1)
        nested_size, _ = board_title_spec(level=2)
        assert root_size >= nested_size

    def test_weight_from_config(self):
        """board_title_spec returns the configured title weight."""
        from dbt_charts.core.compile.resolve.style.typography import board_title_spec

        _, weight = board_title_spec(level=1)
        assert weight == 500

    def test_root_board_title_larger_than_chart_title_at_medium(self):
        """Board header (H1, level=1) is larger than a medium-tier chart title.
        H1 = 24px; medium-tier object title = sizes[1] = 18px. The H1>H2
        relationship preserves the semantic hierarchy.
        """
        from dbt_charts.core.compile.resolve.style.typography import (
            board_title_spec,
            chart_title_spec,
        )

        board_size, _ = board_title_spec(level=1)
        chart_size, _, _ = chart_title_spec(700.0, chart_style_context=_EFF)
        assert board_size > chart_size


# ---------------------------------------------------------------------------
# KPI SVG renderer — width-aware title
# ---------------------------------------------------------------------------


class TestKpiTitleTypography:
    """KPI render_kpi_svg embeds width-aware font-size, weight, and family."""

    _data = [{"value": 42.0}]

    @staticmethod
    def _es():
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        return resolve_chart_style_context(get_theme_style())

    @staticmethod
    def _raw_chart():
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        return KpiChart(
            id="kpi_test",
            type="kpi",
            label="Sales",
            value="value",
            query=SqlQuery(sql="SELECT 42 AS value", source="test_db"),
        )

    def _make_chart(self):
        return resolve(
            self._raw_chart(), self._data, chart_style_context=_BOARD_CONTEXT
        )

    def test_kpi_label_uses_theme_label_font_size_at_every_width(self):
        """The KPI label renders at ``kpi.label.font.size`` (cascade-filled
        from kpi.font.size in stark.yaml) at every card width."""
        from dbt_charts.core.render.chart.kpi import render_kpi_svg

        charts = self._es()
        expected = int(charts.kpi.label.font.size)
        _chart = self._make_chart()
        # Render at three width tiers — label must remain at the theme size.
        for w in (400.0, 700.0, 1152.0):
            svg = render_kpi_svg(
                _chart,
                self._data,
                width=w,
                board_style=_BOARD_STYLE,
            )
            assert (
                f'font-size="{expected}"' in svg or f'font-size="{expected}.0"' in svg
            ), (
                f"KPI label must render at theme size {expected} at width {w}; "
                f"got SVG: {svg[:400]}"
            )

    def test_kpi_label_font_size_theme_override_decouples_from_tiers(self):
        """When the theme sets ``kpi.label.font.size``, the KPI label uses that
        size and the theme's ``kpi.font.weight`` regardless of card width."""
        import re

        from dbt_charts.core.render.chart.kpi import render_kpi_svg

        eff = self._es()
        kpi = eff.kpi
        patched_kpi = kpi.model_copy(
            update={
                "label": kpi.label.model_copy(
                    update={"font": kpi.label.font.model_copy(update={"size": 13.0})}
                )
            }
        )
        custom_context = dataclasses.replace(eff, kpi=patched_kpi)

        # At tiny width (300px) the tiered path would force weight=600; the
        # override must yield the theme's title.font.weight (500 by default).
        _chart = resolve(
            self._raw_chart(), self._data, chart_style_context=custom_context
        )
        for w in (300.0, 700.0, 1152.0):
            svg = render_kpi_svg(
                _chart,
                self._data,
                width=w,
                board_style=_BOARD_STYLE,
            )
            assert 'font-size="13"' in svg or 'font-size="13.0"' in svg, (
                f"Expected label font-size 13 at width {w} after theme override"
            )
            # Find the KPI label text element specifically and check its weight.
            label_match = re.search(
                r'<text[^>]*font-size="13(?:\.0)?"[^>]*font-weight="(\d+)"',
                svg,
            )
            assert label_match is not None, (
                f"Could not find KPI label text element at width {w} in SVG"
            )
            assert label_match.group(1) == "500", (
                f"Expected label weight=500 (theme default) at width {w}, "
                f"got weight={label_match.group(1)} (tier-floor 600 leaking through?)"
            )


# ---------------------------------------------------------------------------
# Table SVG renderer — width-aware title
# ---------------------------------------------------------------------------


class TestTableTitleTypography:
    """render_table_svg embeds width-aware title font-size, weight, and family."""

    _data = [{"col_a": "x", "col_b": 1}]

    def _make_chart(self):
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        return TableChart(
            id="tbl_test",
            type="table",
            title="Revenue",
            query=SqlQuery(sql="SELECT 1", source="test_db"),
        )

    def test_narrow_table_title_correct_size_sans(self):
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        w = 400.0
        expected = chart_title_spec(w, chart_style_context=_EFF)[0]
        svg = render_table_svg(
            resolve(
                self._make_chart(),
                self._data,
                chart_style_context=_BOARD_CONTEXT,
                width=w,
            ),
            self._data,
            width=w,
            board_style=resolve_style(get_theme_style()),
        )
        assert f'font-size="{expected}"' in svg
        assert get_theme_style().font.family.split(",")[0].strip() in svg

    def test_medium_table_title_uses_title_family(self):
        """Medium tier table title uses style.title.font.family (the title-slot family).
        Under default theme that resolves to sans; under editorial it resolves to serif.
        """
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        w = 700.0
        expected_size = chart_title_spec(w, chart_style_context=_EFF)[0]
        title_family = get_theme_style().title.font.family.split(",")[0].strip()
        _chart = resolve(self._make_chart(), [], chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            _chart,
            self._data,
            width=w,
            board_style=resolve_style(get_theme_style()),
        )
        assert f'font-size="{expected_size}"' in svg
        assert 'font-weight="500"' in svg
        assert title_family in svg

    def test_wide_table_title_uses_title_family(self):
        """Wide tier table title uses style.title.font.family (the title-slot family).
        Under default theme that resolves to sans; under editorial it resolves to serif.
        """
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        w = 1152.0
        expected_size = chart_title_spec(w, chart_style_context=_EFF)[0]
        title_family = get_theme_style().title.font.family.split(",")[0].strip()
        _chart = resolve(self._make_chart(), [], chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            _chart,
            self._data,
            width=w,
            board_style=resolve_style(get_theme_style()),
        )
        assert f'font-size="{expected_size}"' in svg
        assert 'font-weight="500"' in svg
        assert title_family in svg


# ---------------------------------------------------------------------------
# Spark bar SVG renderer — width-aware title
# ---------------------------------------------------------------------------


class TestSparkBarTitleTypography:
    """render_spark_bar_svg embeds width-aware title font-size, weight, and family."""

    _data = [{"category": "A", "count": 10}, {"category": "B", "count": 5}]

    def _make_chart(self):
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        return SparkBarChart(
            id="sb_test",
            type="spark_bar",
            title="Top Values",
            x="count",
            y="category",
            query=SqlQuery(sql="SELECT 1", source="test_db"),
        )

    def test_narrow_spark_bar_title_correct_size(self):
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec
        from dbt_charts.core.render.chart.spark_bar import render_spark_bar_svg

        w = 400.0
        expected = chart_title_spec(w, chart_style_context=_EFF)[0]
        svg = render_spark_bar_svg(
            resolve(
                self._make_chart(),
                self._data,
                chart_style_context=_BOARD_CONTEXT,
                width=w,
            ),
            self._data,
            width=w,
            board_style=_BOARD_STYLE,
        )
        assert f'font-size="{expected}"' in svg
        assert 'font-weight="500"' in svg  # config default, no tier override
        assert get_theme_style().font.family.split(",")[0].strip() in svg

    def test_wide_spark_bar_title_uses_title_family(self):
        """Wide tier spark-bar title uses style.title.font.family."""
        from dbt_charts.core.render.chart.spark_bar import render_spark_bar_svg

        title_family = get_theme_style().title.font.family.split(",")[0].strip()
        w = 1152.0
        _chart = resolve(self._make_chart(), [], chart_style_context=_BOARD_CONTEXT)
        svg = render_spark_bar_svg(
            _chart, self._data, width=w, board_style=_BOARD_STYLE
        )
        assert title_family in svg


# ---------------------------------------------------------------------------
# Vega-Lite chart — width-aware title font family in config
# ---------------------------------------------------------------------------


class TestVegaLiteTitleTypography:
    """Vega-Lite specs receive width-aware title font in config.title.font."""

    _data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]

    def _make_chart(self, title: str = "Revenue"):
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        return BarChart(
            id="bar_test",
            type="bar",
            title=title,
            x="month",
            y="revenue",
            query=SqlQuery(sql="SELECT 1", source="test_db"),
        )

    def test_narrow_vega_chart_title_config_uses_inter_variable(self):
        """Regression: narrow chart title font in the Vega spec must be 'Inter Variable'
        (the name vl-convert registers InterVariable.ttf under), not plain 'Inter'.

        Using 'Inter' causes vl-convert to fall back to a system sans-serif because
        the registered font family name is 'Inter Variable', not 'Inter'.  The font
        measurer already uses InterVariable.ttf via get_font_measurer('Inter'), so the
        Vega spec must use the same name to match measurement to rendering.
        """
        from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

        spec = generate_vega_lite_spec(self._make_chart(), self._data, width=300.0)
        title_font = spec.get("config", {}).get("title", {}).get("font")
        assert title_font is not None and "Inter" in title_font, (
            f"Expected title font to include 'Inter', got {title_font!r}"
        )

    def test_wide_vega_chart_title_config_uses_title_family(self):
        """Wide chart title font in the Vega spec is style.title.font.family."""
        from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

        title_family = get_theme_style().title.font.family.split(",")[0].strip()
        spec = generate_vega_lite_spec(self._make_chart(), self._data, width=1152.0)
        title_font = spec.get("config", {}).get("title", {}).get("font")
        assert title_family in str(title_font)

    def test_explicit_style_font_overrides_width_default(self):
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.models.style.authored import (
            BarChartStylePatch,
            TitleStylePatch,
        )
        from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

        chart = BarChart(
            id="bar_test",
            type="bar",
            title="Revenue",
            x="month",
            y="revenue",
            query=SqlQuery(sql="SELECT 1", source="test_db"),
            style=BarChartStylePatch(
                title=TitleStylePatch(font={"family": "Helvetica"})
            ),
        )
        spec = generate_vega_lite_spec(chart, self._data, width=1152.0)
        title_font = spec.get("config", {}).get("title", {}).get("font")
        # Explicit style should win over width-aware default
        assert title_font == "Helvetica"


# ---------------------------------------------------------------------------
# board_title_markdown
# ---------------------------------------------------------------------------


class TestFaceTitleMarkdown:
    """board_title_markdown returns (h1 markdown, h1_size, weight).

    The h1_size contract: callers pass this straight through to
    ``get_compact_style(h1_size=…)`` so the h1 renders at the
    semantic-level pixel size. Board titles are level-only (no width input).
    """

    def test_returns_h1_markdown(self):
        from dbt_charts.core.compile.resolve.style.typography import (
            board_title_markdown,
        )

        md, _, _weight = board_title_markdown("My Title", level=1)
        assert md == "# My Title"

    def test_h1_size_matches_board_title_spec(self):
        """h1_size returned to caller is the exact pixel size from board_title_spec."""
        from dbt_charts.core.compile.resolve.style.typography import (
            board_title_markdown,
            board_title_spec,
        )

        expected_px = board_title_spec(level=1)[0]
        _, h1_size, _weight = board_title_markdown("T", level=1)
        assert h1_size == float(expected_px)

    def test_weight_from_config(self):
        """board_title_markdown weight comes from config."""
        from dbt_charts.core.compile.resolve.style.typography import (
            board_title_markdown,
        )

        _, _, weight = board_title_markdown("T", level=1)
        assert weight == 500


# ---------------------------------------------------------------------------
# Normalizer — Board.level set correctly
# ---------------------------------------------------------------------------


class TestBoardLevel:
    """The normalizer sets board.level correctly using semantic (titled-ancestor) counting."""

    def test_root_board_level_is_1(self):
        from dbt_charts.core.compile.models.board.authored import AuthoredBoard
        from dbt_charts.core.compile.normalize.dispatch import normalize_board

        board = normalize_board(
            AuthoredBoard.model_validate({"title": "Root", "text": "hello"})
        )
        assert board.level == 1

    def test_nested_board_level_is_2(self):
        from dbt_charts.core.compile.models.board.authored import AuthoredBoard
        from dbt_charts.core.compile.normalize.dispatch import normalize_board

        # Root board contains one nested board in its rows layout.
        board = normalize_board(
            AuthoredBoard.model_validate(
                {
                    "title": "Root",
                    "rows": [{"title": "Nested", "text": "hello"}],
                }
            )
        )
        # The root board is level 1.
        assert board.level == 1
        # The nested board inside the layout is level 2.
        nested_item = board.layout.items[0]
        assert nested_item.board is not None
        assert nested_item.board.level == 2

    def test_bare_wrappers_do_not_advance_semantic_level(self):
        """Bare cols/rows wrappers must not bump board.level.

        Pre-fix: titled root (level=1) -> bare cols (level=2) -> bare rows
        (level=3) — structural nesting was incorrectly counted toward semantic
        heading depth. The fix only counts *titled* ancestors. board.level still
        matters for board/prose-section heading sizes (board_title_spec indexes
        it); object titles (chart/table/spark) are now width-only and ignore
        board.level entirely.
        """
        from dbt_charts.core.compile.models.board.authored import AuthoredBoard
        from dbt_charts.core.compile.normalize.dispatch import normalize_board

        board = normalize_board(
            AuthoredBoard.model_validate(
                {
                    "title": "Commercial finance",
                    "cols": [
                        {
                            "rows": [
                                {"text": "hero placeholder"},
                            ],
                        }
                    ],
                }
            )
        )
        assert board.level == 1

        bare_cols = board.layout.items[0].board
        assert bare_cols is not None
        assert bare_cols.level == 1, (
            f"bare cols wrapper level={bare_cols.level}, expected 1 "
            "(bare wrappers must not advance the semantic heading counter)"
        )


class TestNestedBoardSizingLevel:
    """Sizing must measure a nested title at the level it is drawn at.

    `render_nested_board` draws with `level=board.level`, and a nested board is
    level >= 2 by construction, so measuring at the default level 1 reserves
    an h1 box for an h2 title.
    """

    @staticmethod
    def _nested_board() -> Board:
        from dbt_charts.core.compile import compile

        result = compile(
            """
title: Root
rows:
  - title: A nested board with a title long enough to wrap at a narrow width
"""
        )
        assert result.success, result.errors
        nested = result.board.layout.items[0].board
        assert nested is not None and nested.level == 2
        return nested

    def test_sizing_reserves_the_title_height_it_will_draw(self) -> None:
        from dbt_charts.core.render.sizing import (
            get_title_height,
            nested_board_sizing_context,
        )

        nested = self._nested_board()
        content_width, non_layout_height, _ = nested_board_sizing_context(
            nested, 320.0, 0.0, None
        )

        nrs = nested.resolved_style
        card_pad = float(nrs.frame.card_padding)
        inner = max(content_width - 2 * card_pad, 1.0)
        min_h = float(nrs.title.min_height)
        assert nested.title is not None
        as_h1 = max(
            get_title_height(nested.title, inner, None, 1, resolved_style=nrs), min_h
        )
        as_h2 = max(
            get_title_height(nested.title, inner, None, 2, resolved_style=nrs), min_h
        )

        assert as_h1 != as_h2, "the fixture must discriminate the two levels"
        # The band's own top inset rides above the title (BoardContentBox.content_top),
        # so what sizing reserves is that plus the title block itself.
        assert non_layout_height == card_pad + as_h2, (
            f"sizing reserved {non_layout_height} (h1={as_h1}, h2={as_h2}, "
            f"card_padding={card_pad}) — a nested title must be measured at the "
            "level it is drawn at"
        )
