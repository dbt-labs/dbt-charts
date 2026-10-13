"""Routing tests: `render_chart` must dispatch kpi/table/spark_bar/callout to V2.

Wave 0 built the 4 non-VL `render_*_svg` renderers + `_resolve_*` +
`normalize_chart` dispatch, but `_V2_SUPPORTED` still excluded these
families, so `render_chart` never took the V2 branch for them. This suite
pins `render_chart`'s public dispatch: rendering an authored chart through
`render_chart(...)` for each non-VL family must be byte-identical to calling
the family's `render_*_svg` renderer directly.

Both sides resolve from the SAME authored chart def via the real pipelines
(normalize_chart / normalize_chart -> resolve) — never hand-construct a
Resolved*Chart directly, that would hide a resolver gap.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from dbt_charts.core.compile.config import (
    get_default_theme_name,
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.chart.resolved.callout import ResolvedCalloutChart
from dbt_charts.core.compile.models.chart.resolved.kpi import ResolvedKpiChart
from dbt_charts.core.compile.models.chart.resolved.spark_bar import (
    ResolvedSparkBarChart,
)
from dbt_charts.core.compile.models.chart.resolved.table import ResolvedTableChart
from dbt_charts.core.compile.models.query.normalized import ValuesQuery
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.normalize.charts import normalize_chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.render.chart.vega_lite import render_chart
from dbt_charts.core.render.engine import render_native

from ..._engine_render import render_kpi_svg, render_spark_bar_svg, render_table_svg

# Oracle type: (resolved, data, width, height, board_style) -> svg string
_Oracle = Callable[
    [ResolvedChart, list[dict[str, Any]], float, float, ResolvedStyle], str
]


def _oracle_kpi(
    resolved: ResolvedChart,
    data: list[dict[str, Any]],
    width: float,
    height: float,
    board_style: ResolvedStyle,
) -> str:
    assert isinstance(resolved, ResolvedKpiChart)
    return render_kpi_svg(resolved, data, width, height, board_style=board_style)


def _oracle_table(
    resolved: ResolvedChart,
    data: list[dict[str, Any]],
    width: float,
    height: float,
    board_style: ResolvedStyle,
) -> str:
    assert isinstance(resolved, ResolvedTableChart)
    return render_table_svg(resolved, data, width, height, board_style=board_style)


def _oracle_spark_bar(
    resolved: ResolvedChart,
    data: list[dict[str, Any]],
    width: float,
    height: float,
    board_style: ResolvedStyle,
) -> str:
    assert isinstance(resolved, ResolvedSparkBarChart)
    return render_spark_bar_svg(resolved, data, width, height, board_style=board_style)


def _oracle_callout(
    resolved: ResolvedChart,
    data: list[dict[str, Any]],
    width: float,
    height: float,
    board_style: ResolvedStyle,
) -> str:
    assert isinstance(resolved, ResolvedCalloutChart)
    return render_native(
        resolved,
        data,
        width=width,
        height=height,
        padding=None,
        board_style=board_style,
        board_width=width,
    )[0]


def _board_style() -> ResolvedStyle:
    return resolve_style(get_theme_style(get_default_theme_name()))


def _board_ctx() -> ChartStyleContext:
    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


_FAMILIES: dict[str, dict[str, Any]] = {
    "kpi": {
        "chart_id": "k1",
        "chart_def": {
            "type": "kpi",
            "query": "q",
            "value": "revenue",
            "label": "Revenue",
        },
        "query_registry": {"q": ValuesQuery(rows=[{"revenue": 128_000}])},
        "oracle": _oracle_kpi,
    },
    "table": {
        "chart_id": "t1",
        "chart_def": {"type": "table", "query": "q"},
        "query_registry": {
            "q": ValuesQuery(
                rows=[{"region": "US", "amount": 100}, {"region": "EU", "amount": 200}]
            )
        },
        "oracle": _oracle_table,
    },
    "spark_bar": {
        "chart_id": "s1",
        "chart_def": {"type": "spark_bar", "query": "q", "x": "value", "y": "month"},
        "query_registry": {
            "q": ValuesQuery(
                rows=[{"month": "Jan", "value": 10}, {"month": "Feb", "value": 20}]
            )
        },
        "oracle": _oracle_spark_bar,
    },
    "callout": {
        "chart_id": "c1",
        "chart_def": {"type": "callout", "message": "All systems normal"},
        "query_registry": {},
        "oracle": _oracle_callout,
    },
}


def _route_and_compare(
    monkeypatch: pytest.MonkeyPatch,
    family: str,
    chart_def: dict[str, Any],
    query_registry: dict[str, Any],
    data: list[dict[str, Any]],
) -> None:
    spec = _FAMILIES[family]
    chart_id = spec["chart_id"]
    board_style = _board_style()
    board_ctx = _board_ctx()

    flat = normalize_chart(chart_id, chart_def, query_registry, sources={})
    v2_model = flat

    reset_config()

    # Spy on BoardRenderSession.render_svg_family (unconditionally, no
    # raising=False guard): pre-routing, the method does not exist yet on
    # BoardRenderSession, so this fails loudly with AttributeError — the
    # correct RED for "render_chart never reaches the session for this
    # family". A byte-equality check alone can't prove routing since v1/v2
    # SVG output is required to be identical.
    from dbt_charts.core.render.chart.session import BoardRenderSession

    calls: list[Any] = []
    original = BoardRenderSession.render_svg_family

    def _spy(self: Any, *args: Any, **kwargs: Any) -> str | None:
        calls.append((args, kwargs))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(BoardRenderSession, "render_svg_family", _spy)

    actual = render_chart(
        flat, board_style, board_ctx, data, format="svg", width=400, height=200
    )

    assert calls, (
        "render_chart did not route through BoardRenderSession.render_svg_family"
    )

    resolved = resolve(v2_model, data, chart_style_context=board_ctx)
    expected = spec["oracle"](resolved, data, 400, 200, board_style)
    assert actual == expected


@pytest.mark.parametrize("family", ["kpi", "table", "spark_bar", "callout"])
def test_render_chart_routes_family_through_v2_svg(
    monkeypatch: pytest.MonkeyPatch, family: str
) -> None:
    """render_chart must produce SVG byte-identical to render_<family>_svg_v2."""
    spec = _FAMILIES[family]
    _route_and_compare(
        monkeypatch,
        family,
        spec["chart_def"],
        spec["query_registry"],
        _data_for(family),
    )


def _data_for(family: str) -> list[dict[str, Any]]:
    registry = _FAMILIES[family]["query_registry"]
    if not registry:
        return []
    chart_def = _FAMILIES[family]["chart_def"]
    query_name = chart_def.get("query") or next(iter(registry))
    return registry[query_name].rows


def test_render_chart_routes_kpi_conditional_formatting_through_v2_svg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """kpi fixture with a background gradient channel routes byte-identically."""
    chart_def = {
        "type": "kpi",
        "query": "q",
        "value": "revenue",
        "label": "Revenue",
        "background": {
            "column": "revenue",
            "scale": {"palette": ["#ffffff", "#3366cc"]},
        },
    }
    query_registry = {"q": ValuesQuery(rows=[{"revenue": 128_000}])}
    _route_and_compare(
        monkeypatch, "kpi", chart_def, query_registry, query_registry["q"].rows
    )


def test_render_chart_routes_table_conditional_formatting_through_v2_svg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """table fixture with per-cell conditional_formatting routes byte-identically."""
    chart_def = {
        "type": "table",
        "query": "q",
        "conditional_formatting": {
            "amount": {"when": [{"lt": 0, "background": "#fee2e2"}]}
        },
    }
    query_registry = {"q": ValuesQuery(rows=[{"amount": -500}, {"amount": 200}])}
    _route_and_compare(
        monkeypatch, "table", chart_def, query_registry, query_registry["q"].rows
    )


@pytest.mark.parametrize("family", ["kpi", "table", "spark_bar", "callout"])
def test_get_emitter_rejects_non_vl(family: str) -> None:
    """get_emitter has no arms for non-VL families — they raise an ERR-coded RenderError."""
    from dbt_charts.core.render.chart.emitters import get_emitter
    from dbt_charts.core.render.errors import RenderError

    spec = _FAMILIES[family]
    data = _data_for(family)
    v2_model = normalize_chart(
        spec["chart_id"], spec["chart_def"], spec["query_registry"], sources={}
    )
    resolved = resolve(v2_model, data, chart_style_context=_board_ctx())
    with pytest.raises(RenderError, match="No emitter registered"):
        get_emitter(resolved)
