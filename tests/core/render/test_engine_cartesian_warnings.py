"""Cartesian render-time warnings the engine emits land in their Python sinks.

Every test is a real `dbt_charts._engine` call; the mapping from warning code
to sink is `engine._WARNING_SINKS`.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any

from dbt_charts.core.compile.models.board.normalized import NO_TITLE_SHIFT
from dbt_charts.core.compile.models.query.normalized import ValuesQuery
from dbt_charts.core.compile.normalize.charts import normalize_chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.render.chart.axis_label_collision import (
    AxisLabelCollision,
    collect_axis_label_collisions,
)
from dbt_charts.core.render.chart.endpoint_label_overflow import (
    EndpointLabelGapOverflow,
    collect_endpoint_label_gap_overflows,
)
from dbt_charts.core.render.chart.series_label_truncation import (
    SeriesLabelTruncation,
    collect_series_label_truncations,
)
from dbt_charts.core.render.chart.text_truncation import (
    TextTruncation,
    collect_text_truncations,
)
from dbt_charts.core.render.chart.x_domain_paint_order import (
    XDomainPaintOrder,
    collect_x_domain_paint_orders,
)
from dbt_charts.core.render.engine import _render_payload, build_chart_payload

from .test_engine import _BOARD_CTX, _BOARD_RS, _BOARD_WIDTH, _native


@dataclass
class Sinks:
    truncations: dict[str, list[TextTruncation]]
    series_labels: dict[str, list[SeriesLabelTruncation]]
    rail_overflows: dict[str, EndpointLabelGapOverflow | None]
    collisions: dict[str, AxisLabelCollision]
    paint_orders: dict[str, XDomainPaintOrder]


@contextmanager
def _sinks() -> Generator[Sinks]:
    with ExitStack() as stack:
        yield Sinks(
            stack.enter_context(collect_text_truncations()),
            stack.enter_context(collect_series_label_truncations()),
            stack.enter_context(collect_endpoint_label_gap_overflows()),
            stack.enter_context(collect_axis_label_collisions()),
            stack.enter_context(collect_x_domain_paint_orders()),
        )


_RAIL_LINE = {
    "type": "line",
    "query": "q",
    "x": "month",
    "y": "revenue",
    "color": "region",
}


def _rail_rows(ends: tuple[int, int, int]) -> list[dict[str, Any]]:
    return [
        {"month": month, "region": region, "revenue": value}
        for month in ("Jan", "Feb")
        for region, value in zip(("EMEA", "AMER", "APAC"), ends, strict=True)
    ]


def test_rail_label_truncation_lands_in_the_series_label_sink() -> None:
    name = "Accessories - Average order value across every region"
    rows = [
        {"month": m, "region": r, "revenue": v}
        for m in ("Jan", "Feb")
        for r, v in (("EMEA", 5), (name, 7))
    ]
    with _sinks() as sinks:
        _native(_RAIL_LINE, rows, width=600.0, height=320.0)
    assert sinks.series_labels == {
        "c1": [SeriesLabelTruncation(authored_field="color", series_name=name)]
    }


def test_rail_gap_that_does_not_fit_lands_in_the_overflow_sink() -> None:
    with _sinks() as sinks:
        _native(_RAIL_LINE, _rail_rows((10, 20, 30)), width=600.0, height=60.0)
    assert sinks.rail_overflows == {
        "c1": EndpointLabelGapOverflow(
            series_count=3, gap_px=18.2, cause="gap_did_not_fit"
        )
    }


def test_rail_label_with_no_room_lands_in_the_overflow_sink_as_dropped() -> None:
    chart = {
        **_RAIL_LINE,
        "style": {"axis_y": {"scale": {"continuous": {"domain": [0, 100]}}}},
    }
    with _sinks() as sinks:
        _native(chart, _rail_rows((45, 46, 47)), width=600.0, height=80.0)
    assert sinks.rail_overflows == {
        "c1": EndpointLabelGapOverflow(
            series_count=3,
            gap_px=18.2,
            cause="rail_overflow",
            dropped_series=("AMER",),
        )
    }


def test_rail_on_one_value_lands_in_the_overflow_sink_as_no_slope() -> None:
    with _sinks() as sinks:
        _native(_RAIL_LINE, _rail_rows((50, 50, 50)), width=600.0, height=320.0)
    assert sinks.rail_overflows == {
        "c1": EndpointLabelGapOverflow(series_count=3, gap_px=18.2, cause="no_slope")
    }


def test_scatter_category_labels_no_tilt_fits_land_in_the_collision_sink() -> None:
    rows = [{"segment": f"Segment {i:02}", "revenue": i * 10} for i in range(80)]
    scatter = {"type": "scatter", "query": "q", "x": "segment", "y": "revenue"}
    with _sinks() as sinks:
        _native(scatter, rows, width=600.0, height=320.0)
    assert sinks.collisions == {
        "c1": AxisLabelCollision(field="segment", label_count=80)
    }


def test_line_temporal_labels_no_tilt_fits_land_in_the_collision_sink() -> None:
    rows = [
        {"month": f"{2015 + i // 12}-{i % 12 + 1:02}-01", "revenue": i}
        for i in range(80)
    ]
    line = {
        "type": "line",
        "query": "q",
        "x": "month",
        "y": "revenue",
        "style": {"axis_x": {"labels": {"overlap": {"skip": False, "tilt": True}}}},
    }
    with _sinks() as sinks:
        _native(line, rows, width=600.0, height=320.0)
    assert sinks.collisions == {"c1": AxisLabelCollision(field="month", label_count=80)}


def test_layer_x_values_the_base_does_not_order_land_in_the_paint_order_sink() -> None:
    base = [{"month": m, "revenue": 1} for m in ("Jan", "Mar", "May")]
    projected = [{"month": m, "target": 2} for m in ("Feb", "Apr", "Jun")]
    registry = {"q": ValuesQuery(rows=base), "p": ValuesQuery(rows=projected)}
    chart_def = {
        "type": "bar",
        "query": "q",
        "x": "month",
        "y": "revenue",
        "layers": [{"type": "bar", "query": "p", "y": "target"}],
    }
    chart = resolve(
        normalize_chart("c1", chart_def, registry, sources={}),
        base,
        chart_style_context=_BOARD_CTX,
    )
    payload = build_chart_payload(
        chart,
        {"q": base, "p": projected},
        width=600.0,
        height=320.0,
        padding=None,
        board_style=_BOARD_RS,
        board_width=_BOARD_WIDTH,
        variables={},
        title_shift=NO_TITLE_SHIFT,
        is_placeholder=False,
    )
    with _sinks() as sinks:
        _render_payload(payload)
    assert sinks.paint_orders == {
        "c1": XDomainPaintOrder(x_field="month", layer_only=("Feb", "Apr", "Jun"))
    }


_STRIP_ROWS = [
    {"product": f"Product {i:02}", "revenue": 1_234_567 + i, "units": 12_345 + i}
    for i in range(12)
]


def _strip_bar() -> dict[str, Any]:
    return {
        "type": "bar",
        "query": "q",
        "x": "product",
        "y": "revenue",
        "support_table": [
            {"source": "revenue", "format": "currency_whole", "label": "Revenue"},
            {"source": "units", "format": ",d", "label": "Units"},
        ],
        "style": {"orientation": "vertical"},
    }


def test_support_table_cells_wider_than_their_band_land_in_the_text_sink() -> None:
    with _sinks() as sinks:
        _native(_strip_bar(), _STRIP_ROWS, width=600.0, height=400.0)
    [truncation] = sinks.truncations["c1"]
    assert (truncation.surface, truncation.authored_field) == (
        "support_table_cell_overflow",
        "support_table",
    )
    assert "px needed" in truncation.authored_text
