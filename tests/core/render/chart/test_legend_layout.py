"""TDD tests: focused legend label-wrapping regression coverage."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import BarChart, Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    BarChartStylePatch,
    LegendStylePatch,
)
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

# Endpoint labels off — a stacked bar with a series color channel otherwise
# suppresses the legend entirely in favor of direct labeling, which would
# leave every legend-layout assertion below with no legend to inspect.
_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(
    get_theme_style().model_copy(
        update={
            "charts": get_theme_style().charts.model_copy(
                update={
                    "bar": get_theme_style().charts.bar.model_copy(
                        update={
                            "endpoint_labels": get_theme_style().charts.bar.endpoint_labels.model_copy(
                                update={"visible": False}
                            )
                        }
                    )
                }
            )
        }
    )
)

_DUMMY_QUERY = SqlQuery(sql="SELECT 1", source="test")

SAMPLE_DATA = [
    {"month": "Jan", "revenue": 100, "category": "A"},
    {"month": "Feb", "revenue": 200, "category": "B"},
]


@pytest.fixture(autouse=True)
def stark_fixture(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin to stark so legend defaults remain stable for the regression."""
    reset_config()
    monkeypatch.setenv("DCT_DEFAULT_THEME", "stark")
    yield
    reset_config()


def _bar_chart(**kwargs) -> Chart:
    """Stacked by default — a grouped bar (the family default) forces its
    legend to a top/horizontal, untitled layout regardless of any legend
    style patch, which would confound these generic legend-layout tests."""
    defaults: dict[str, Any] = {
        "id": "test_legend",
        "query": _DUMMY_QUERY,
        "query_name": "q",
        "type": "bar",
        "x": "month",
        "y": "revenue",
        "color": "category",
        "stack": "zero",
    }
    defaults.update(kwargs)
    return BarChart(**defaults)


def _color_legend(spec: dict[str, Any]) -> dict[str, Any] | None:
    return spec.get("encoding", {}).get("color", {}).get("legend")


def _vl_spec(chart: Chart) -> dict[str, Any]:
    return generate_vega_lite_spec(
        chart, SAMPLE_DATA, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CTX
    )


class TestLegendLabelMaxWidth:
    def test_label_max_width_emits_label_limit(self):
        chart = _bar_chart(
            style=BarChartStylePatch.model_validate(
                {
                    "legend": LegendStylePatch.model_validate(
                        {"label": {"max_width": 1000}}
                    )
                }
            )
        )
        spec = _vl_spec(chart)
        legend = _color_legend(spec)
        assert legend is not None
        assert legend["labelLimit"] == 1000

    def test_board_level_label_max_width_overrides_theme(self):
        chart = _bar_chart(
            style=BarChartStylePatch.model_validate(
                {
                    "legend": LegendStylePatch.model_validate(
                        {"label": {"max_width": 500}}
                    )
                }
            )
        )
        spec = _vl_spec(chart)
        legend = _color_legend(spec)
        assert legend is not None
        assert legend["labelLimit"] == 500

    def test_bottom_orient_with_explicit_max_width(self):
        chart = _bar_chart(
            style=BarChartStylePatch.model_validate(
                {
                    "legend": LegendStylePatch.model_validate(
                        {"position": {"edge": "bottom"}, "label": {"max_width": 1000}}
                    )
                }
            )
        )
        spec = _vl_spec(chart)
        legend = _color_legend(spec)
        assert legend is not None
        assert legend["labelLimit"] == 1000
        assert legend["orient"] == "bottom"


class TestLegendLabelPadding:
    """Legend label padding is the gap between a legend symbol and its text.

    Maps the dbt charts ``legend.label.padding`` to Vega-Lite ``labelOffset``
    (the offset of a legend label from its symbol). Regression: the value was
    declared in the theme model but never emitted, so wide stroke symbols
    (target lines in mixed bar+line legends) rendered flush against their text.
    """

    def test_label_padding_emits_label_offset(self):
        chart = _bar_chart(
            style=BarChartStylePatch.model_validate(
                {"legend": LegendStylePatch.model_validate({"label": {"padding": 13}})}
            )
        )
        spec = _vl_spec(chart)
        legend = _color_legend(spec)
        assert legend is not None
        assert legend["labelOffset"] == 13

    def test_default_label_padding_emits_positive_offset(self):
        """The theme default must produce a real gap (the bug: no gap at all)."""
        chart = _bar_chart()
        spec = _vl_spec(chart)
        legend = _color_legend(spec)
        assert legend is not None
        assert "labelOffset" in legend
        assert legend["labelOffset"] > 0


class TestLegendTitleVisible:
    """legend.title.visible: false suppresses the legend title (axis.title.visible analog).

    The suppression lives in the legend config (VL ``legend.title = null``), which
    removes the title from layout while keeping ``encoding.color.title`` intact for
    aria-labels and tooltips — mirroring how axis.title.visible keeps encoding.title.
    """

    def _color_title(self, spec: dict[str, Any]) -> Any:
        return spec.get("encoding", {}).get("color", {}).get("title")

    def test_title_shown_by_default(self):
        """By default the legend carries no title override; the title flows from
        encoding.color.title (the formatted field name)."""
        chart = _bar_chart()
        spec = _vl_spec(chart)
        legend = _color_legend(spec)
        assert legend is not None
        assert "title" not in legend
        assert self._color_title(spec)  # formatted field name present

    def test_title_visible_false_suppresses_title(self):
        chart = _bar_chart(
            style=BarChartStylePatch.model_validate(
                {
                    "legend": LegendStylePatch.model_validate(
                        {"title": {"visible": False}}
                    )
                }
            )
        )
        spec = _vl_spec(chart)
        legend = _color_legend(spec)
        assert legend is not None
        assert legend["title"] is None  # VL legend.title = null hides the title
        # encoding.color.title is preserved for aria-labels / tooltips
        assert self._color_title(spec)


class TestLegendSymbolLimit:
    """Legend symbolLimit caps high-cardinality series to prevent overflow."""

    def test_default_symbol_limit_emitted_in_legend(self):
        """The default theme symbol_limit is emitted as VL symbolLimit."""
        chart = _bar_chart()
        spec = _vl_spec(chart)
        legend = _color_legend(spec)
        assert legend is not None
        assert "symbolLimit" in legend

    def test_board_level_symbol_limit_overrides_theme(self):
        """Board-level symbol_limit patch overrides the theme default."""
        chart = _bar_chart(
            style=BarChartStylePatch.model_validate(
                {"legend": LegendStylePatch.model_validate({"symbol_limit": 10})}
            )
        )
        spec = _vl_spec(chart)
        legend = _color_legend(spec)
        assert legend is not None
        assert legend["symbolLimit"] == 10

    def test_symbol_limit_unset_in_theme_omits_key(self):
        """When the theme carries no symbol_limit, symbolLimit is absent from the VL spec."""
        from dbt_charts.core.compile.models.primitives import ResolvedFontStyle
        from dbt_charts.core.compile.models.style.resolved import (
            ResolvedLegendElementStyle,
            ResolvedLegendPosition,
            ResolvedLegendStyle,
        )
        from dbt_charts.core.render.chart.vl_field_maps import legend_to_vl

        _font = ResolvedFontStyle(
            family="sans-serif",
            color="#000",
            size=12.0,
            weight="400",
            style="normal",
            decoration="none",
            case="none",
            line_height=1.25,
            tabular_figures=False,
        )
        _elem = ResolvedLegendElementStyle(font=_font, padding=4.0, visible=True)
        legend_no_cap = ResolvedLegendStyle(
            position=ResolvedLegendPosition(edge="right", align="start", overlay=False),
            direction="vertical",
            columns=0,
            compact_columns=2,
            label=_elem,
            title=_elem,
            visible=True,
            symbol_limit=None,
        )
        result = legend_to_vl(legend_no_cap)
        assert result is not None
        assert "symbolLimit" not in result
