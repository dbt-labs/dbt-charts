"""Regression tests: palette tokens resolve in chart-level style and conditional_formatting.

Chart-local style overrides (ChartStylePatch) and conditional_formatting rules can
reference palette tokens like ``dbt-creams.7``. Before the fix, those tokens
leaked through to the renderer as literal strings (e.g. stroke="dbt-creams.7").

Coverage:
- Axis: axis_x.ticks.color, axis_y.grid.color, axis_quantitative.label.font.color
- Top-level chart style: background, color
- Title / legend: title.font.color, legend.label.font.color
- Per-chart-type: bar.stroke, kpi.background, spark_bar.bar.color
- Borders: kpi.border.color
- Table: table.background, table.row.font.color, table.header.background
- Conditional formatting: rules[0].background, rules[0].font.color
- Failure case: unknown token raises UnknownColorError at compile time
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    KpiChart,
    SparkBarChart,
    TableChart,
)
from dbt_charts.core.compile.models.style.authored import (
    ChartStylePatch,
)
from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)

TOKEN = "dbt-creams.7"
HEX = "#918878"
UNKNOWN = "dbt-creams.does-not-exist"


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_config()
    yield
    reset_config()


def _board():
    return resolve_chart_style_context(get_theme_style())


def _patch(d: dict):
    """Extract the per-family style patch from a ChartStylePatch-shape dict."""
    monolithic = ChartStylePatch.model_validate(d)
    key = next(iter(d))
    return getattr(monolithic, key)


# ── Axis fields ───────────────────────────────────────────────────────────────


class TestAxisTokenResolution:
    def test_axis_x_ticks_color(self) -> None:
        patch = _patch({"bar": {"axis_x": {"ticks": {"color": TOKEN}}}})
        effective = build_chart_style_context(
            _board(), BarChart(id="t", type="bar", style=patch)
        )
        merged = resolved_axis_style(
            effective, "axis_x", "ordinal", chart_type="", labeled=False
        )
        assert merged.ticks.color == HEX

    def test_axis_y_grid_color(self) -> None:
        patch = _patch({"bar": {"axis_y": {"grid": {"color": TOKEN}}}})
        effective = build_chart_style_context(
            _board(), BarChart(id="t", type="bar", style=patch)
        )
        merged = resolved_axis_style(
            effective, "axis_y", "quantitative", chart_type="", labeled=False
        )
        assert merged.grid.color == HEX

    def test_axis_quantitative_label_font_color(self) -> None:
        patch = _patch(
            {"bar": {"axis_quantitative": {"labels": {"font": {"color": TOKEN}}}}}
        )
        effective = build_chart_style_context(
            _board(), BarChart(id="t", type="bar", style=patch)
        )
        merged = resolved_axis_style(
            effective, "axis_y", "quantitative", chart_type="", labeled=False
        )
        assert merged.labels.font.color == HEX

    def test_unknown_axis_token_raises(self) -> None:
        from dbt_charts.core.compile.resolve.style.palette import UnknownColorError

        patch = _patch({"bar": {"axis_x": {"ticks": {"color": UNKNOWN}}}})
        with pytest.raises(UnknownColorError):
            build_chart_style_context(
                _board(), BarChart(id="t", type="bar", style=patch)
            )


# ── Top-level chart style ─────────────────────────────────────────────────────


class TestTopLevelChartStyleTokenResolution:
    def test_background_token(self) -> None:
        effective = build_chart_style_context(
            _board(),
            BarChart(id="t", type="bar", style=_patch({"bar": {"background": TOKEN}})),
        )
        assert effective.background == HEX

    def test_color_token(self) -> None:
        effective = build_chart_style_context(
            _board(),
            BarChart(
                id="t",
                type="bar",
                style=_patch({"bar": {"marks": {"bar": {"color": TOKEN}}}}),
            ),
        )
        assert effective.bar.marks.bar.color == HEX


# ── Title and legend ──────────────────────────────────────────────────────────


class TestTitleLegendTokenResolution:
    def test_title_font_color_token(self) -> None:
        effective = build_chart_style_context(
            _board(),
            BarChart(
                id="t",
                type="bar",
                style=_patch({"bar": {"title": {"font": {"color": TOKEN}}}}),
            ),
        )
        assert effective.title.font.color == HEX

    def test_legend_label_font_color_token(self) -> None:
        effective = build_chart_style_context(
            _board(),
            BarChart(
                id="t",
                type="bar",
                style=_patch(
                    {"bar": {"legend": {"label": {"font": {"color": TOKEN}}}}}
                ),
            ),
        )
        assert effective.legend.label.font.color == HEX


# ── Per-chart-type overlays ───────────────────────────────────────────────────


class TestPerChartTypeTokenResolution:
    def test_bar_axis_x_ticks_color_token(self) -> None:
        # bar.axis_x.ticks.color proves per-chart-type token resolution at chart level.
        effective = build_chart_style_context(
            _board(),
            BarChart(
                id="t",
                type="bar",
                style=_patch({"bar": {"axis_x": {"ticks": {"color": TOKEN}}}}),
            ),
        )
        assert effective.bar.axis_x.ticks.color == HEX

    def test_kpi_font_color_token(self) -> None:
        effective = build_chart_style_context(
            _board(),
            KpiChart(
                id="t",
                type="kpi",
                value="v",
                style=_patch({"kpi": {"font": {"color": TOKEN}}}),
            ),
        )
        assert effective.kpi.font.color == HEX

    def test_spark_bar_bar_color_token(self) -> None:
        effective = build_chart_style_context(
            _board(),
            SparkBarChart(
                id="t",
                type="spark_bar",
                style=_patch({"spark_bar": {"bar": {"color": TOKEN}}}),
            ),
        )
        assert effective.spark_bar.bar.color == HEX


# ── Borders ───────────────────────────────────────────────────────────────────


class TestBorderColorTokenResolution:
    def test_kpi_border_color_token(self) -> None:
        effective = build_chart_style_context(
            _board(),
            KpiChart(
                id="t",
                type="kpi",
                value="v",
                style=_patch({"kpi": {"border": {"color": TOKEN}}}),
            ),
        )
        assert effective.kpi.border.color == HEX


# ── Table fields ──────────────────────────────────────────────────────────────


class TestTableTokenResolution:
    def test_table_background_token(self) -> None:
        effective = build_chart_style_context(
            _board(),
            TableChart(
                id="t", type="table", style=_patch({"table": {"background": TOKEN}})
            ),
        )
        assert effective.table.background == HEX

    def test_table_header_background_token(self) -> None:
        effective = build_chart_style_context(
            _board(),
            TableChart(
                id="t",
                type="table",
                style=_patch({"table": {"header": {"background": TOKEN}}}),
            ),
        )
        assert effective.table.header.background == HEX

    def test_table_row_stripe_color_token(self) -> None:
        effective = build_chart_style_context(
            _board(),
            TableChart(
                id="t",
                type="table",
                style=_patch({"table": {"row": {"stripe": {"color": TOKEN}}}}),
            ),
        )
        assert effective.table.row.stripe is not None
        assert effective.table.row.stripe.color == HEX


# ── Conditional formatting ────────────────────────────────────────────────────


class TestConditionalFormattingTokenResolution:
    """Tokens in a table column's ``conditional`` rules resolve at style-resolution time.

    Normalization carries the token through unresolved: a role-indirected
    token (e.g. ``category[1]``) needs the active theme's palettes/roles
    context, which only exists once resolve() runs against a
    ChartStyleContext, not at normalize_chart() time. See GH
    dbt-labs/dbt-charts#34.
    """

    def _normalized_chart(self, text: dict):
        from dbt_charts.core.compile.normalize.charts import normalize_chart

        return normalize_chart(
            chart_id="test_chart",
            chart_def={
                "type": "table",
                "query": {"columns": ["x"], "values": [["a"], ["b"]]},
                "style": {"columns": {"x": {"conditional": [{"eq": "x", **text}]}}},
            },
            query_registry={},
            sources={},
        )

    def _resolved_rule(self, text: dict):
        from dbt_charts.core.compile.resolve import resolve

        chart = self._normalized_chart(text)
        resolved = resolve(chart, [{"x": "a"}], chart_style_context=_board())
        return resolved.columns["x"].conditional[0]

    def test_normalize_leaves_background_token_unresolved(self) -> None:
        chart = self._normalized_chart({"background": TOKEN})
        assert chart.style.columns["x"].conditional[0].background == TOKEN

    def test_rule_background_token_resolves_at_resolve_time(self) -> None:
        rule = self._resolved_rule({"background": TOKEN})
        assert rule.background == HEX

    def test_rule_font_color_token_resolves_at_resolve_time(self) -> None:
        rule = self._resolved_rule({"font": {"color": TOKEN}})
        assert rule.font.color == HEX

    def test_unknown_background_token_raises_at_resolve_time(self) -> None:
        from dbt_charts.core.compile.errors import CompilationError

        with pytest.raises(CompilationError, match="'test_chart'"):
            self._resolved_rule({"background": UNKNOWN})

    def test_bracket_role_token_in_font_color_resolves(self) -> None:
        """The exact crash reported in GH dbt-labs/dbt-charts#34: category[1] in
        a conditional rule raised UnknownColorError because normalize-time
        resolution had no theme palettes/roles context. It now resolves like
        every other role-indirected token."""
        from dbt_charts.core.compile.resolve.style.tokens import (
            _resolve_one_color_token,
        )

        board = _board()
        rule = self._resolved_rule({"font": {"color": "category[1]"}})
        assert rule.font.color == _resolve_one_color_token(
            "category[1]", board.palettes, board.roles
        )

    def test_predicate_value_shaped_like_a_token_is_left_alone(self) -> None:
        """A scalar predicate operand or glyph is data/text, not a color --
        even when its value happens to be shaped like a color token, it must
        not be silently rewritten to hex (no magic). This does not cover a
        token-shaped value inside a list predicate (e.g. `in:`) -- the
        keyed-collection guard is field-name-based, and a list carries no
        field name to check."""
        from dbt_charts.core.compile.normalize.charts import normalize_chart
        from dbt_charts.core.compile.resolve import resolve

        chart = normalize_chart(
            chart_id="test_chart",
            chart_def={
                "type": "table",
                "query": {"columns": ["x"], "values": [["a"], ["b"]]},
                "style": {
                    "columns": {
                        "x": {
                            "conditional": [
                                {
                                    "eq": "category[1]",
                                    "glyph": "positive.solid",
                                    "background": TOKEN,
                                }
                            ]
                        }
                    }
                },
            },
            query_registry={},
            sources={},
        )
        resolved = resolve(chart, [{"x": "a"}], chart_style_context=_board())
        rule = resolved.columns["x"].conditional[0]
        assert rule.eq == "category[1]"
        assert rule.glyph == "positive.solid"
        assert rule.background == HEX


# ── Bracket role tokens (theme-portable) ─────────────────────────────────────


class TestBracketRoleTokensInChartStyle:
    """Bracket role tokens (1-indexed, e.g. ``category[3].dark``) resolve in
    chart-level style through the ACTIVE theme's ``style.palettes`` role
    bindings — the theme-portable counterpart of the absolute tokens above.
    A board converted from explicit ``editorial-10.*`` stops to role tokens
    must repaint on a theme switch, including per-chart style blocks. The
    variant segment applies live (variant()), no companion file needed.
    """

    THEME_FAMILY = (
        ("stark", "vivid-10"),
        ("clarity", "editorial-10"),
        ("paper", "editorial-10"),
    )

    @pytest.mark.parametrize(("theme", "family"), THEME_FAMILY)
    def test_chart_palette_list_follows_theme(self, theme: str, family: str) -> None:
        from dbt_charts.core.compile.resolve.style.palette import (
            palette as resolve_palette,
            variant,
        )

        board = resolve_chart_style_context(get_theme_style(theme))
        patch = _patch(
            {
                "bar": {
                    "color": {
                        "categorical": {"palette": ["category[3].dark", "category[1]"]}
                    }
                }
            }
        )
        effective = build_chart_style_context(
            board, BarChart(id="t", type="bar", style=patch)
        )
        assert list(effective.palette) == [
            variant(resolve_palette(family)[2], "dark"),
            resolve_palette(family)[0],
        ], f"chart-level bracket tokens must follow the {theme} theme's roles"

    @pytest.mark.parametrize(("theme", "family"), THEME_FAMILY)
    def test_chart_background_bracket_token_follows_theme(
        self, theme: str, family: str
    ) -> None:
        from dbt_charts.core.compile.resolve.style.palette import (
            palette as resolve_palette,
            variant,
        )

        board = resolve_chart_style_context(get_theme_style(theme))
        patch = _patch({"bar": {"background": "category[2].pale"}})
        effective = build_chart_style_context(
            board, BarChart(id="t", type="bar", style=patch)
        )
        assert effective.background == variant(resolve_palette(family)[1], "pale")

    def test_unknown_role_bracket_token_raises(self) -> None:
        from dbt_charts.core.compile.resolve.style.palette import UnknownColorError

        board = resolve_chart_style_context(get_theme_style("stark"))
        patch = _patch({"bar": {"background": "categry[2]"}})
        with pytest.raises(UnknownColorError):
            build_chart_style_context(board, BarChart(id="t", type="bar", style=patch))


class TestUnknownAliasOnCatalogPalette:
    """`dbt-grays.surface` names a shipped palette with no such alias: the
    error must say so, not that `dbt-grays` is an unbound theme role."""

    def test_unknown_alias_reports_the_palette_aliases(self) -> None:
        from dbt_charts.core.compile.resolve.style.palette import UnknownColorError
        from dbt_charts.core.compile.resolve.style.tokens import (
            _resolve_one_color_token,
        )

        with pytest.raises(
            UnknownColorError,
            match=r"palette 'dbt-grays' has no alias 'surface'. Known aliases",
        ):
            _resolve_one_color_token(
                "dbt-grays.surface", {"category": "vivid-10"}, None
            )

    def test_a_theme_role_sharing_the_catalog_name_still_resolves(self) -> None:
        from dbt_charts.core.compile.resolve.style.tokens import (
            _resolve_one_color_token,
        )

        assert _resolve_one_color_token(
            "positive.solid", {"positive": "dbt-positive"}, None
        ).startswith("#")
